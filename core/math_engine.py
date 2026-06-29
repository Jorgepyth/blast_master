import math
import os
from decimal import Decimal, getcontext

getcontext().prec = 18

def calculate_probabilities(calc_edge: float) -> tuple:
    scaling_factor = float(os.getenv("TACTICAL_SCALING_FACTOR", 0.2833))
    no_trade_exponent = float(os.getenv("NO_TRADE_BASE_EXPONENT", 1.54))
    
    e_long = math.exp(calc_edge * scaling_factor)
    e_short = math.exp(-calc_edge * scaling_factor)
    e_no_trade = math.exp(no_trade_exponent)
    
    total = e_long + e_short + e_no_trade
    long_prob = round(e_long / total, 3)
    short_prob = round(e_short / total, 3)
    no_trade_prob = round(1.0 - long_prob - short_prob, 3)
    return long_prob, short_prob, no_trade_prob

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
    
    dist_to_sl = sl_dist / dep
    dist_to_tp = tp_dist / dep
    
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
