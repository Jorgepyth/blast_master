"""
tools/pnl_calculator.py — Conversion precio->dinero para el flujo tactical_audit.

Modulo AISLADO a proposito:
  - NO importa rich (la presentacion vive en cli/).
  - NO hace ninguna llamada de red (verificable interceptando socket en tests).
  - NO importa core/math_engine.py (que tiene su propia copia historica de esta
    aritmetica y esta off-limits).

Es la UNICA fuente de verdad de esta formula en el repo. Consumidores:
  - cli/schemas/audit_tactical.py  -> validator risk_usd/notional_size_usd (Fase 2)
  - cli/main.py                    -> cuadro de P&L efimero en flow_pending_audits (Fase 3)
  - tools/apply_historical_size_migration.py -> recalculo de risk_usd (Track B Paso 3)

contract_size se lee de config/contract_specs.py (tabla estatica versionada en git),
nunca de una API de broker.

Formula:
    monto_usd = |precio_entrada - precio_objetivo| * size_lots * contract_size(asset)

Todo el calculo es en Decimal. Nunca float.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from config.contract_specs import CONTRACT_SPECS, SYMBOL_ALIASES

_MT5_SPEC_HINT = (
    "clic derecho sobre el simbolo en Market Watch de MT5 -> Specification"
)


class UnknownSymbolError(Exception):
    """El asset no tiene entrada (ni via alias) en CONTRACT_SPECS."""


class UnverifiedSymbolError(Exception):
    """El simbolo existe pero su contract_size no esta VERIFICADO / es None."""


def _d(x) -> Decimal:
    """Coercion robusta a Decimal, siempre via str -> nunca arrastra ruido de float."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def resolve_spec(asset: str) -> dict:
    """
    Resuelve `asset` (string tal cual vive en unified_department.asset, ej.
    'XAUUSDT.P') a su spec de contract_size, aplicando SYMBOL_ALIASES primero.

    Devuelve {"symbol", "contract_size" (Decimal), "source"}.

    Levanta:
      - UnknownSymbolError    si no hay clave en CONTRACT_SPECS.
      - UnverifiedSymbolError si confidence != 'VERIFICADO' o contract_size is None.
    El mensaje siempre nombra el simbolo y dice como verificarlo en MT5.
    """
    if asset is None:
        raise UnknownSymbolError("asset is None: no hay simbolo que resolver.")

    key = SYMBOL_ALIASES.get(asset, asset)
    spec = CONTRACT_SPECS.get(key)
    if spec is None:
        raise UnknownSymbolError(
            f"Simbolo {asset!r} (resuelto a {key!r}) no esta en "
            f"config/contract_specs.CONTRACT_SPECS. Agregarlo tras verificar su "
            f"contract size en MT5 ({_MT5_SPEC_HINT})."
        )

    contract_size = spec.get("contract_size")
    if spec.get("confidence") != "VERIFICADO" or contract_size is None:
        raise UnverifiedSymbolError(
            f"contract_size de {asset!r} (resuelto a {key!r}) esta NO VERIFICADO "
            f"(confidence={spec.get('confidence')!r}, contract_size={contract_size!r}). "
            f"No se calcula P&L hasta confirmarlo manualmente en MT5 ({_MT5_SPEC_HINT}) "
            f"y marcarlo 'VERIFICADO' en config/contract_specs.py."
        )

    return {
        "symbol": key,
        "contract_size": _d(contract_size),
        "source": spec.get("source"),
    }


def leg_usd(entry_price, target_price, size_lots, contract_size) -> Decimal:
    """|entry - target| * size_lots * contract_size. Todo Decimal."""
    ep = _d(entry_price)
    target = _d(target_price)
    size = _d(size_lots)
    cs = _d(contract_size)
    return abs(ep - target) * size * cs


def quantify(asset, entry_price, size_lots, stop_loss, take_profit) -> dict:
    """
    P&L potencial en USD (vista efimera; este modulo no persiste nada).

    -> {"symbol", "contract_size", "loss_usd", "gain_usd", "rr"}
       Todo Decimal; `rr` es Decimal o None (None si loss_usd <= 0).

    Levanta UnknownSymbolError / UnverifiedSymbolError via resolve_spec -> nunca
    calcula en silencio con un contract_size sin confirmar.
    """
    spec = resolve_spec(asset)
    cs = spec["contract_size"]

    loss_usd = leg_usd(entry_price, stop_loss, size_lots, cs)
    gain_usd = leg_usd(entry_price, take_profit, size_lots, cs)
    rr: Optional[Decimal] = (gain_usd / loss_usd) if loss_usd > 0 else None

    return {
        "symbol": spec["symbol"],
        "contract_size": cs,
        "loss_usd": loss_usd,
        "gain_usd": gain_usd,
        "rr": rr,
    }
