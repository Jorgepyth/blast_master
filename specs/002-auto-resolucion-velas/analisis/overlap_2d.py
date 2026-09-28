"""
Read-only analysis for spec 002, F3 [2][5]: Overlap candidates and a 2-day (48 h) evaluation window.

Reads the real account DBs with `mode=ro&immutable=1` and the MT5 CSV exports. Writes nothing except the
Markdown report passed as argv[1].

Rules (spec 002 decisions):
  - anchor = created_at - 20 min (N4); backdated rows use created_at, the typed time (N5).
  - starting price = close of the last closed 15M bar at the anchor.
  - forward path = 15M bars opening at/after the anchor, then 1H bars once 15M ends (no validated 1M/5M yet).
  - Overlap candidate = another analysis B of the same account (backdated included, N22) whose anchor is after
    A's anchor and before A's first touch (N11).
  - 48 h window: the first touch counts only if it happens within 48 h of A's anchor ("from A") or within
    48 h of B's anchor ("from B").

Usage: conda run -n blast_master python overlap_2d.py <output.md>
"""
import sqlite3
import sys

import pandas as pd

BASE = "/mnt/c/Users/jcifu/MT5Exports"
DATA = "/home/jorgecg/projects/trading/blast_master/.data"
ACCOUNTS = [("XAU", f"{DATA}/flight_account_001_xauusd.db", "XAUUSD"),
            ("BTC", f"{DATA}/flight_account_002_btcusdtp.db", "BTCUSD")]
LEAD = pd.Timedelta(minutes=20)
WINDOW_H = 48


def load(sym, tf):
    df = pd.read_csv(f"{BASE}/{sym}/{tf}.csv")
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def build_rows():
    recs = []
    for acc, db, sym in ACCOUNTS:
        m15, h1 = load(sym, "15M"), load(sym, "1H")
        path_all = pd.concat([m15[["time", "high", "low"]],
                              h1[h1["time"] > m15["time"].iloc[-1]][["time", "high", "low"]]]).reset_index(drop=True)
        end_candles = path_all["time"].iloc[-1]
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        rows = con.execute(
            """select u.id, u.created_at, u.is_backdated, u.market_bias, u.edge_validation_price,
                      u.structural_invalidation, e.bias_a, e.real_bias_b, e.specific_bias_compliance,
                      e.resolution_type
               from unified_department u left join efficiency_audit e on e.id = u.id
               order by u.created_at""").fetchall()
        con.close()
        anchor = lambda ca, bd: pd.Timestamp(ca) if bd else pd.Timestamp(ca) - LEAD
        all_anchors = [(r[0], anchor(r[1], r[2])) for r in rows]
        for tid, ca, bd, mb, evp, si, ba, rb, sbc, rt in rows:
            a = anchor(ca, bd)
            rec = dict(acc=acc, id=tid[:8], created=str(ca)[:16], retro=bool(bd), market_bias=mb, bias_a=ba,
                       real_bias_b=rb, compliance=sbc, manual_type=(rt or "").split(" (")[0], state=None)
            if evp is None or si is None:
                rec["state"] = "no_levels"; recs.append(rec); continue
            prev = m15[m15["time"] + pd.Timedelta(minutes=15) <= a]
            if prev.empty:
                rec["state"] = "no_history"; recs.append(rec); continue
            ref, evp, si = float(prev["close"].iloc[-1]), float(evp), float(si)
            if not ((evp > ref > si) or (evp < ref < si)):
                rec["state"] = "levels_same_side"; recs.append(rec); continue
            rec["thesis"] = "long" if evp > ref else "short"
            path = path_all[path_all["time"] >= a]
            up, lo = max(evp, si), min(evp, si)
            hit = path[(path["high"] >= up) | (path["low"] <= lo)]
            first, t = None, None
            if not hit.empty:
                bar = hit.iloc[0]; t = bar["time"]
                if bar["high"] >= up and bar["low"] <= lo:
                    first = "ambiguous"
                else:
                    first = "VALIDATION" if (bar["high"] >= up) == (up == evp) else "INVALIDATION"
            limit = t if t is not None else end_candles
            later = [(bid, ba_) for bid, ba_ in all_anchors if bid != tid and a < ba_ < limit]
            rec.update(first=first or "none", h_touch=(t - a).total_seconds() / 3600 if t is not None else None)
            if later:
                bid, b_anchor = min(later, key=lambda x: x[1])
                rec.update(b_id=bid[:8], h_a_to_b=(b_anchor - a).total_seconds() / 3600)
            rec["state"] = "overlap" if (later and t is not None) else "resolved" if t is not None else "no_touch"
            recs.append(rec)
    return pd.DataFrame(recs)


def within(r, start_h):
    """First touch within WINDOW_H hours counted from start_h (hours after A's anchor)."""
    if r["first"] not in ("VALIDATION", "INVALIDATION") or pd.isna(r["h_touch"]):
        return "—"
    return r["first"] if r["h_touch"] - start_h <= WINDOW_H else "—"


def wr(d, col):
    d = d[d[col].isin(["VALIDATION", "INVALIDATION"])]
    n, v = len(d), int((d[col] == "VALIDATION").sum())
    return (f"{v}/{n} = {v / n:.0%}" if n else "—"), n


def main(out_path):
    df = build_rows()
    ov = df[df["state"] == "overlap"].copy()
    ov["w_a"] = ov.apply(lambda r: within(r, 0.0), axis=1)
    ov["w_b"] = ov.apply(lambda r: within(r, r["h_a_to_b"]), axis=1)
    df["w_a_all"] = df.apply(lambda r: within(r, 0.0) if r["state"] in ("resolved", "overlap") else "—", axis=1)
    df["first_all"] = df.apply(lambda r: r.get("first") if r["state"] in ("resolved", "overlap") else "—", axis=1)

    L = []
    A = L.append
    A("# Overlap con ventana de 2 días (F3 [2][5], opción del usuario)")
    A("")
    A("- **Fecha:** 2026-09-27. Solo lectura sobre las DBs reales y los CSV de MT5.")
    A("- **Script:** `specs/002-auto-resolucion-velas/analisis/overlap_2d.py`, reproducible con")
    A("  `conda run -n blast_master python overlap_2d.py <salida.md>`.")
    A("- **Reglas:**")
    A("  - ancla `created_at − 20 min`; en los retroactivos, la hora tipeada;")
    A("  - velas 15M y, cuando se acaban, 1H (todavía no hay 1M ni 5M validados), así que la hora del toque tiene una")
    A("    precisión de ±15 min;")
    A("  - B puede ser retroactivo (N22).")
    A("- **Tu opción:** marcar Overlap y **seguir evaluando** si el precio llega a validation o invalidation dentro de")
    A("  2 días. Hay dos formas de medir esos 2 días, y las muestro las dos:")
    A("  - **desde A:** 48 h desde el inicio de A. Es una \"vida útil\" de 2 días para la tesis;")
    A("  - **desde B:** 48 h desde que empezó B, el momento del Overlap.")
    A("- **Retroactivos:** fuera de las cifras de acierto (R11).")
    A("")
    A("## Los 21 candidatos a Overlap, con la ventana de 2 días")
    A("")
    A("| # | Cuenta | ID A | Creado A | Retro | Market bias A | Tipo manual | Compliance | ID B | h A→B | Primer toque (sin límite) | h A→toque | ¿Tocó en 48 h desde A? | ¿Tocó en 48 h desde B? |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (_, r) in enumerate(ov.iterrows(), 1):
        A(f"| {i} | {r['acc']} | {r['id']} | {r['created']} | {'sí' if r['retro'] else ''} | {r['market_bias']} | "
          f"{r['manual_type']} | {r['compliance']} | {r['b_id']} | {r['h_a_to_b']:.1f} | {r['first']} | "
          f"{r['h_touch']:.1f} | {r['w_a']} | {r['w_b']} |")
    A("")
    for col, label in (("w_a", "desde A"), ("w_b", "desde B")):
        c = ov[col].value_counts().to_dict()
        A(f"- **48 h {label}:** VALIDATION {c.get('VALIDATION', 0)}, INVALIDATION {c.get('INVALIDATION', 0)} y "
          f"sin toque {c.get('—', 0)}.")
    A("")
    A("## Win rate por velas según cada criterio (sin retroactivos)")
    A("")
    A("Win rate = tocó validation primero, sobre los análisis que tocaron un nivel. \"Fuera\" son los que el criterio "
      "deja sin resultado.")
    A("")
    A("| Criterio | Cuenta | Win rate | Fuera |")
    A("|---|---|---|---|")
    base = df[df["state"].isin(["resolved", "overlap"]) & ~df["retro"]]
    for acc in ("XAU", "BTC"):
        b = base[base["acc"] == acc]
        res, ovl = b[b["state"] == "resolved"], b[b["state"] == "overlap"]
        ovl_a = ovl.assign(x=ovl.apply(lambda r: within(r, 0.0), axis=1))
        ovl_b = ovl.assign(x=ovl.apply(lambda r: within(r, r["h_a_to_b"]), axis=1))
        scen = [
            ("S1: primer toque de todos, sin límite", b.assign(x=b["first"])),
            ("S2: sin los Overlap", res.assign(x=res["first"])),
            ("S3: Overlap solo si tocó en 48 h **desde A**; el resto sin límite",
             pd.concat([res.assign(x=res["first"]), ovl_a])),
            ("S3b: Overlap solo si tocó en 48 h **desde B**; el resto sin límite",
             pd.concat([res.assign(x=res["first"]), ovl_b])),
            ("S4: ventana de 48 h desde A para **todos**", b.assign(x=b["w_a_all"])),
        ]
        for label, d in scen:
            w, n = wr(d, "x")
            A(f"| {label} | {acc} | {w} | {len(d) - n} |")
    A("")
    A("## Win rate solo de análisis direccionales (Bullish o Bearish), sin retroactivos")
    A("")
    A("Mismos criterios que la tabla anterior, pero sin los análisis `Choppy / Neutral`. **Win rate manual** es la "
      "proporción de `specific_bias_compliance = Valid` sobre **los mismos análisis** que cuenta cada criterio.")
    A("")
    A("| Criterio | Activo | Win rate por velas | Win rate manual (mismos análisis) | Fuera |")
    A("|---|---|---|---|---|")
    dirb = df[df["market_bias"].isin(["Bullish", "Bearish"]) & ~df["retro"]]
    dbase = dirb[dirb["state"].isin(["resolved", "overlap"])]

    def man(d):
        d = d[d["compliance"].isin(["Valid", "Invalid"])]
        n, v = len(d), int((d["compliance"] == "Valid").sum())
        return f"{v}/{n} = {v / n:.0%}" if n else "—"

    originals = []
    for acc in ("XAU", "BTC"):
        originals.append((acc, man(dirb[dirb["acc"] == acc]), dirb[dirb["acc"] == acc]["state"].value_counts().to_dict()))
        b = dbase[dbase["acc"] == acc]
        res, ovl = b[b["state"] == "resolved"], b[b["state"] == "overlap"]
        scen = [
            ("S1: primer toque de todos, sin límite", b.assign(x=b["first"])),
            ("S2: sin los Overlap", res.assign(x=res["first"])),
            ("S3: Overlap solo si tocó en 48 h desde A", pd.concat([res.assign(x=res["first"]),
                                                                   ovl.assign(x=ovl.apply(lambda r: within(r, 0.0), axis=1))])),
            ("S3b: Overlap solo si tocó en 48 h desde B", pd.concat([res.assign(x=res["first"]),
                                                                    ovl.assign(x=ovl.apply(lambda r: within(r, r["h_a_to_b"]), axis=1))])),
            ("S4: ventana de 48 h desde A para todos", b.assign(x=b.apply(lambda r: within(r, 0.0), axis=1))),
        ]
        for label, d in scen:
            w, n = wr(d, "x")
            inc = d[d["x"].isin(["VALIDATION", "INVALIDATION"])]
            A(f"| {label} | {acc} | {w} | {man(inc)} | {len(d) - n} |")
    A("")
    A("**Estado original** (win rate manual de **todos** los análisis Bullish/Bearish sin retroactivos, tengan o no "
      "resultado por velas):")
    A("")
    for acc, m, states in originals:
        A(f"- **{acc}:** {m}. Por estado: {states}.")
    mism = dbase[((dbase["market_bias"] == "Bullish") & (dbase["thesis"] == "short")) |
                 ((dbase["market_bias"] == "Bearish") & (dbase["thesis"] == "long"))]
    A("")
    A(f"**Coherencia bias ↔ niveles:** en {len(mism)} de {len(dbase)} análisis direccionales, la dirección que marcan tus "
      "niveles (validation arriba = long) contradice el market bias." + (" IDs: " + ", ".join(mism["id"]) + "." if len(mism) else ""))
    A("")
    A("## Tu etiqueta manual contra las velas (todos los resueltos, sin retroactivos)")
    A("")
    A("`specific_bias_compliance` compara bias estructurales (A contra B), y las velas miran qué nivel se tocó primero. "
      "La correspondencia Valid ↔ VALIDATION es **aproximada**. Estos son los casos donde no coinciden, para tu "
      "revisión:")
    A("")
    dis = base[((base["compliance"] == "Valid") & (base["first"] == "INVALIDATION")) |
               ((base["compliance"] == "Invalid") & (base["first"] == "VALIDATION"))]
    A("| # | Cuenta | ID | Creado | Market bias | Bias A | Real bias B | Compliance | Tipo manual | Primer toque (velas) | h A→toque | ¿Overlap? |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (_, r) in enumerate(dis.iterrows(), 1):
        A(f"| {i} | {r['acc']} | {r['id']} | {r['created']} | {r['market_bias']} | {r['bias_a']} | {r['real_bias_b']} | "
          f"{r['compliance']} | {r['manual_type']} | {r['first']} | {r['h_touch']:.1f} | "
          f"{'sí' if r['state'] == 'overlap' else ''} |")
    A("")
    for acc in ("XAU", "BTC"):
        b = base[base["acc"] == acc]
        b = b[b["compliance"].isin(["Valid", "Invalid"])]
        agree = int((((b["compliance"] == "Valid") & (b["first"] == "VALIDATION")) |
                     ((b["compliance"] == "Invalid") & (b["first"] == "INVALIDATION"))).sum())
        vi = int(((b["compliance"] == "Valid") & (b["first"] == "INVALIDATION")).sum())
        iv = int(((b["compliance"] == "Invalid") & (b["first"] == "VALIDATION")).sum())
        A(f"- **{acc}:** coinciden {agree} de {len(b)}. Marcaste Valid y las velas dicen INVALIDATION: {vi}. Marcaste "
          f"Invalid y las velas dicen VALIDATION: {iv}.")
    A("")
    A("## Lectura (interpretación, no dato)")
    A("")
    A("1. **La ventana de 2 días deja muchos Overlap sin resultado si se cuenta desde A:** 9 de 21. Contada desde B, "
      "solo 3 de 21.")
    A("2. **Con todos los análisis, el win rate de XAU casi no cambia** con ningún criterio: entre 57% y 59%. BTC oscila "
      "entre 53% y 62%, pero con solo 13 a 17 análisis. Con esa muestra, 9 puntos son ruido (CLAUDE.md, checklist "
      "punto 4).")
    A("3. **Solo direccionales:** en XAU, las velas dan 61% (S1) y tu etiqueta manual 63% sobre los mismos 38 análisis. "
      "La diferencia es de 1 análisis. Sin retroactivos, tu etiqueta da 64% (25/39); con los 7 retroactivos daba "
      "68.9% (31/45), la cifra de la auditoría. **La mayor parte de esa diferencia venía de los retroactivos.** En BTC, "
      "con 8 a 12 análisis, cualquier porcentaje es ruido.")
    A("4. **El hallazgo más útil:** de los 8 casos en que tu etiqueta manual no coincide con las velas, **6 son "
      "candidatos a Overlap**. Cuando existía un análisis B posterior, tu etiqueta de A tendió a seguir la vista nueva "
      "(la de B) y no lo que hizo el precio con los niveles de A.")
    A("5. **Caso a revisar:** `887dbdc1` (XAU, 2026-09-01) tiene tipo manual \"Confirmed\" y compliance \"Invalid\". "
      "Las dos etiquetas se contradicen entre sí.")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(df["state"].value_counts().to_dict())


if __name__ == "__main__":
    main(sys.argv[1])
