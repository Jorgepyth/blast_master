"""
config/contract_specs.py — Tabla estática y versionada de contract_size por
símbolo (multiplicador precio→dinero), más el mapeo de los tickers reales de
las DBs de cuenta a esos símbolos.

NUNCA se lee de una API de bróker en vivo (MT5, Binance u otra). Los valores se
derivan una sola vez con `tools/derive_contract_multiplier.py` contra un reporte
de historial MT5, y se confirman manualmente contra el panel "Specification" del
símbolo en MT5 (clic derecho sobre el símbolo en Market Watch -> Specification).

Marcadores de confianza (mismo estándar que ARCHITECTURE.md):
  "VERIFICADO"     -> n >= 30 y desviación relativa < 0.1% en el reporte, contract_size usable.
  "NO VERIFICADO"  -> muestra insuficiente o dispersa; `contract_size` es None y
                      `tools/pnl_calculator.py` RECHAZA calcular con ese símbolo
                      hasta que un humano lo confirme y lo marque "VERIFICADO".

REGLA DURA: `contract_size` es un `Decimal` real o `None`. Nunca un string, nunca
un placeholder tipo "...". `empirical_hint` (si existe) es solo referencia para el
humano que va a verificar — `pnl_calculator` no lo usa jamás.
"""
from decimal import Decimal

# --- generated block: tools/derive_contract_multiplier.py reescribe SOLO esto ---
CONTRACT_SPECS = {
    "XAUUSD": {
        "contract_size": Decimal("100"),
        "confidence": "VERIFICADO",
        "source": "ReportHistory-87050257.xlsx · Positions · n=265 · rel_std<0.1%",
        "derived_at": "2026-09-03",
    },
    "BTCUSD": {
        "contract_size": None,
        "confidence": "NO VERIFICADO",
        "empirical_hint": Decimal("1.000213"),  # solo referencia — nunca usado en el cálculo
        "source": "ReportHistory-87050257.xlsx · Positions · n=5",
        "derived_at": "2026-09-03",
    },
    "NAS100": {
        "contract_size": None,
        "confidence": "NO VERIFICADO",
        "empirical_hint": None,  # n=1, insuficiente incluso como hint
        "source": "ReportHistory-87050257.xlsx · Positions · n=1",
        "derived_at": "2026-09-03",
    },
}
# --- end generated block ---

# Hand-maintained. Mapea `unified_department.asset` (string exacto verificado por
# grep: SELECT DISTINCT asset FROM unified_department) a la clave de CONTRACT_SPECS.
# El renombrado de estos tickers a nomenclatura MT5 estándar es un desarrollo
# futuro y separado — este archivo solo los traduce, no los cambia en la DB.
SYMBOL_ALIASES = {
    "XAUUSDT.P": "XAUUSD",
    "BTCUSDT.P": "BTCUSD",
    "US100": "NAS100",
}
