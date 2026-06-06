import sqlite3
import os

db_path = ".data/flight_account_001_xauusd.db"
if not os.path.exists(db_path):
    print(f"DB NOT FOUND {db_path}")

try:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # 1. Fetch last 5 records
        cur.execute("SELECT id, market_bias, long_prob, short_prob, no_trade_prob FROM unified_department ORDER BY created_at DESC LIMIT 5")
        rows = cur.fetchall()
        rows = rows[::-1]  # oldest to newest
        
        bias_list = []
        l_probs = []
        s_probs = []
        n_probs = []
        
        for r in rows:
            bias = r['market_bias'].upper() if r['market_bias'] else "NEUTRAL"
            if "BULL" in bias or "LONG" in bias:
                color = "green"
            elif "BEAR" in bias or "SHORT" in bias:
                color = "red"
            else:
                color = "yellow"
            
            # Use original text, just capitalized
            display_bias = r['market_bias'].capitalize() if r['market_bias'] else "Neutral"
            bias_list.append(f"[{color}]{display_bias}[/{color}]")
            
            l_probs.append(r['long_prob'] or 0.0)
            s_probs.append(r['short_prob'] or 0.0)
            n_probs.append(r['no_trade_prob'] or 0.0)
            
        print(" -> ".join(bias_list))
        if len(rows) > 0:
            avg_l = sum(l_probs) / len(l_probs)
            avg_s = sum(s_probs) / len(s_probs)
            avg_n = sum(n_probs) / len(n_probs)
            print(f"Avg: Long {avg_l:.1f}% | Short {avg_s:.1f}% | Neutral {avg_n:.1f}%")
            
        # Px Dominance
        # Get IDs
        ids = [r['id'] for r in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            cur.execute(f"SELECT layer_name, direction, strength FROM analysis_layer WHERE trade_id IN ({placeholders})", ids)
            layer_rows = cur.fetchall()
            
            from collections import defaultdict
            # Count directions and strengths per layer
            counts = defaultdict(lambda: {'LONG':0, 'SHORT':0, 'FLAT':0, 'STRONG':0, 'MID':0, 'WEAK':0})
            for lr in layer_rows:
                ln = lr['layer_name']
                d = lr['direction'].upper() if lr['direction'] else "FLAT"
                s = lr['strength'].upper() if lr['strength'] else "WEAK"
                counts[ln][d] += 1
                counts[ln][s] += 1
                
            for k in sorted(counts.keys()):
                print(f"{k}: {counts[k]}")
except Exception as e:
    print("Error:", e)
