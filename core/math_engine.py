import math
import os
from decimal import Decimal, getcontext
from typing import NamedTuple
from dotenv import load_dotenv

# Ensure configuration is loaded using absolute path to project root
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv(dotenv_path=env_path)

getcontext().prec = 18

class Probabilities(NamedTuple):
    long_prob: Decimal
    short_prob: Decimal
    no_trade_prob: Decimal

def calculate_probabilities(calc_edge) -> Probabilities:
    """Calcula las probabilidades direccionales usando un modelo Lineal Conservador."""
    
    # Lectura Estricta de Entorno a Decimal
    try:
        NO_TRADE_MAX = Decimal(str(os.getenv("ICD_NO_TRADE_MAX", "0.8000")))
        NO_TRADE_MIN = Decimal(str(os.getenv("ICD_NO_TRADE_MIN", "0.5000")))
        EDGE_CAP = Decimal(str(os.getenv("ICD_EDGE_CAP", "1.0000")))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Configuración de riesgo inválida en .env: {e}")

    DELTA_CAUTELA = NO_TRADE_MAX - NO_TRADE_MIN
    d_edge = Decimal(str(calc_edge))
    
    # 1. Normalización
    if d_edge > EDGE_CAP:
        edge_norm = EDGE_CAP
    elif d_edge < -EDGE_CAP:
        edge_norm = -EDGE_CAP
    else:
        edge_norm = d_edge / EDGE_CAP
        
    abs_edge = abs(edge_norm)
    
    # 2. Cautela (Hard Boundary)
    # Se utiliza max() pero asegurando que ambos argumentos son estrictamente Decimal
    calculated_nt = NO_TRADE_MAX - (DELTA_CAUTELA * abs_edge)
    no_trade_prob = NO_TRADE_MIN if calculated_nt < NO_TRADE_MIN else calculated_nt
    
    # 3. Presupuesto
    action_prob = Decimal('1.0000') - no_trade_prob
    
    # 4. Distribución
    long_prob = action_prob * (Decimal('0.5') + Decimal('0.5') * edge_norm)
    short_prob = action_prob * (Decimal('0.5') - Decimal('0.5') * edge_norm)
    
    # Validación Crítica
    sum_total = no_trade_prob + long_prob + short_prob
    if abs(sum_total - Decimal('1.0000')) > Decimal('1e-7'):
        raise ValueError(f"Fallo de integridad paramétrica: Suma = {sum_total}")
    
    return Probabilities(long_prob=long_prob, short_prob=short_prob, no_trade_prob=no_trade_prob)

# Funciones heredadas (No modificar)
def calculate_algebraic_metrics(direction: str, ep: float, sl: float, cp: float, tp: float, size: float, mae: float, mfe: float, cost: float = 0.0):
    dep = Decimal(str(ep))
    dsl = Decimal(str(sl))
    dcp = Decimal(str(cp))
    dtp = Decimal(str(tp))
    dsize = Decimal(str(size))
    dmae = Decimal(str(mae))
    dmfe = Decimal(str(mfe))
    dcost = Decimal(str(cost))

    if dep == Decimal('0.0') or dsl == Decimal('0.0'):
        raise ValueError("Entry Price o Stop Loss no pueden ser cero.")
    if dep == dsl:
        raise ValueError("Entry Price y Stop Loss no pueden ser idénticos. Riesgo infinito detectado.")
    if dsize <= Decimal('0.0'):
        raise ValueError("El tamaño de la posición (Size) debe ser mayor a cero.")

    notional_size = dep * dsize
    notional_size_usd = dep * dsize
    sl_dist = abs(dep - dsl)
    tp_dist = abs(dep - dtp)
    
    dist_to_sl = sl_dist / dep if dep != Decimal('0.0') else Decimal('0.0')
    dist_to_tp = tp_dist / dep if dep != Decimal('0.0') else Decimal('0.0')
    
    if direction == "Long":
        capital_at_risk = dsize * (dep - dsl)
        risk_usd = dsize * sl_dist
        pnl = (dcp - dep) * dsize
        r_r = (dtp - dep) / sl_dist if sl_dist != Decimal('0.0') else Decimal('0.0')
        r_multiple = (dcp - dep) / sl_dist if sl_dist != Decimal('0.0') else Decimal('0.0')
    else:
        capital_at_risk = dsize * (dsl - dep)
        risk_usd = dsize * sl_dist
        pnl = (dep - dcp) * dsize
        r_r = (dep - dtp) / sl_dist if sl_dist != Decimal('0.0') else Decimal('0.0')
        r_multiple = (dep - dcp) / sl_dist if sl_dist != Decimal('0.0') else Decimal('0.0')
        
    if r_multiple < Decimal('0.0') or dmfe == Decimal('0.0'):
        captured_mfe = Decimal('0.0')
    else:
        captured_mfe = r_multiple / dmfe
        
    if r_multiple < Decimal('0.0') and dmae > Decimal('0.0'):
        captured_mae = abs(r_multiple) / dmae
    else:
        captured_mae = Decimal('0.0')
        
    return {
        "notional_size": notional_size,
        "notional_size_usd": notional_size_usd,
        "dist_to_sl": dist_to_sl,
        "dist_to_tp": dist_to_tp,
        "capital_at_risk": capital_at_risk,
        "risk_usd": risk_usd,
        "pnl": pnl,
        "pnl_and_cost": pnl - dcost,
        "cost": dcost,
        "r_r": r_r,
        "r_multiple": r_multiple,
        "captured_mfe": captured_mfe,
        "captured_mae": captured_mae
    }
