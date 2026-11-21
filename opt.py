"""Analisi di una catena di opzioni (dati CBOE ritardati). Solo libreria standard."""
import re, datetime

OCC = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")


def f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def parse_chain(doc):
    """Documento CBOE -> (spot, data_di_riferimento, contratti)."""
    data = doc.get("data") or {}
    spot = f(data.get("current_price")) or f(data.get("close"))
    ts = str(doc.get("timestamp") or "")
    try:
        asof = datetime.date.fromisoformat(ts[:10])
    except ValueError:
        asof = datetime.date.today()
    out = []
    for o in data.get("options") or []:
        m = OCC.match(str(o.get("option", "")))
        if not m:
            continue
        y, mo, d = int(m.group(2)[:2]) + 2000, int(m.group(2)[2:4]), int(m.group(2)[4:])
        try:
            exp = datetime.date(y, mo, d)
        except ValueError:
            continue
        bid, ask, last = f(o.get("bid")), f(o.get("ask")), f(o.get("last_trade_price"))
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
        out.append({"exp": exp, "type": m.group(3), "k": int(m.group(4)) / 1000.0, "bid": bid, "ask": ask, "mid": mid,
                    "iv": f(o.get("iv")), "oi": int(f(o.get("open_interest"))), "vol": int(f(o.get("volume"))),
                    "delta": f(o.get("delta")), "gamma": f(o.get("gamma")), "sym": o.get("option")})
    return spot, asof, out


def max_pain(cs):
    ks = sorted({c["k"] for c in cs})
    best, bp = None, None
    for k in ks:
        pay = sum(c["oi"] * max(0.0, k - c["k"]) for c in cs if c["type"] == "C") + \
              sum(c["oi"] * max(0.0, c["k"] - k) for c in cs if c["type"] == "P")
        if bp is None or pay < bp:
            best, bp = k, pay
    return best


def ratio(a, b):
    return None if b == 0 else round(a / b, 2)


def analyse(doc, symbol):
    spot, asof, cs = parse_chain(doc)
    if spot <= 0 or not cs:
        raise ValueError("La catena di opzioni di %s è vuota o non valida." % symbol)
    cs = [c for c in cs if c["exp"] >= asof]
    cv = sum(c["vol"] for c in cs if c["type"] == "C"); pv = sum(c["vol"] for c in cs if c["type"] == "P")
    co = sum(c["oi"] for c in cs if c["type"] == "C"); po = sum(c["oi"] for c in cs if c["type"] == "P")
    cprem = sum(c["vol"] * c["mid"] * 100 for c in cs if c["type"] == "C")
    pprem = sum(c["vol"] * c["mid"] * 100 for c in cs if c["type"] == "P")
    exps = sorted({c["exp"] for c in cs})
    per = []
    for e in exps[:6]:
        ec = [c for c in cs if c["exp"] == e]
        calls = {c["k"]: c for c in ec if c["type"] == "C"}; puts = {c["k"]: c for c in ec if c["type"] == "P"}
        both = sorted(set(calls) & set(puts), key=lambda k: abs(k - spot))
        atm_iv = move = None
        if both:
            k = both[0]
            ivs = [x for x in (calls[k]["iv"], puts[k]["iv"]) if x > 0]
            atm_iv = round(100 * sum(ivs) / len(ivs), 1) if ivs else None
            st = calls[k]["mid"] + puts[k]["mid"]
            move = round(100 * st / spot, 2) if st > 0 else None
        per.append({"exp": e.isoformat(), "dte": (e - asof).days,
                    "cvol": sum(c["vol"] for c in ec if c["type"] == "C"), "pvol": sum(c["vol"] for c in ec if c["type"] == "P"),
                    "coi": sum(c["oi"] for c in ec if c["type"] == "C"), "poi": sum(c["oi"] for c in ec if c["type"] == "P"),
                    "atm_iv": atm_iv, "move_pct": move, "max_pain": max_pain(ec)})
    # muri di open interest sulla prima scadenza con almeno 5 giorni (o la prima)
    near = next((e for e in exps if (e - asof).days >= 5), exps[0])
    nc = [c for c in cs if c["exp"] == near]
    wall_c = sorted([c for c in nc if c["type"] == "C" and c["k"] >= spot], key=lambda c: -c["oi"])[:3]
    wall_p = sorted([c for c in nc if c["type"] == "P" and c["k"] <= spot], key=lambda c: -c["oi"])[:3]
    # attività insolita: tanto volume rispetto all'open interest, con premio rilevante
    un = [c for c in cs if c["vol"] >= 300 and c["vol"] >= c["oi"] and c["vol"] * c["mid"] * 100 >= 50000]
    un.sort(key=lambda c: -c["vol"] * c["mid"])
    unusual = [{"sym": c["sym"], "type": c["type"], "k": c["k"], "exp": c["exp"].isoformat(), "vol": c["vol"], "oi": c["oi"],
                "premium": round(c["vol"] * c["mid"] * 100), "iv": round(c["iv"] * 100, 1)} for c in un[:12]]
    # skew intorno a 30 giorni: IV put con delta ~ -0.25 meno IV call con delta ~ +0.25
    skew = None
    tgt = min(exps, key=lambda e: abs((e - asof).days - 30))
    tc = [c for c in cs if c["exp"] == tgt and c["iv"] > 0]
    pc = [c for c in tc if c["type"] == "P" and c["delta"] < 0]; cc = [c for c in tc if c["type"] == "C" and c["delta"] > 0]
    if pc and cc:
        p25 = min(pc, key=lambda c: abs(c["delta"] + 0.25)); c25 = min(cc, key=lambda c: abs(c["delta"] - 0.25))
        if abs(p25["delta"] + 0.25) < 0.12 and abs(c25["delta"] - 0.25) < 0.12:
            skew = round((p25["iv"] - c25["iv"]) * 100, 1)
    # esposizione gamma stimata (ipotesi classica: i clienti comprano call e put, chi fa mercato è dall'altra parte)
    gex = 0.0
    for c in cs:
        if (c["exp"] - asof).days <= 45:
            gex += (1 if c["type"] == "C" else -1) * c["gamma"] * c["oi"] * 100 * spot * spot * 0.01
    # lettura
    score, why = 0, []
    pcv = ratio(pv, cv)
    if pcv is not None and cv + pv >= 1000:
        if pcv < 0.7: score += 1; why.append("volume call prevalente (put/call %.2f)" % pcv)
        elif pcv > 1.1: score -= 1; why.append("volume put prevalente (put/call %.2f)" % pcv)
        else: why.append("volume call e put equilibrati (put/call %.2f)" % pcv)
    if cprem + pprem > 0:
        sh = cprem / (cprem + pprem)
        if sh > 0.6: score += 1; why.append("il %d%% del premio scambiato è su call" % round(sh * 100))
        elif sh < 0.4: score -= 1; why.append("il %d%% del premio scambiato è su put" % round((1 - sh) * 100))
    if skew is not None:
        if skew > 8: score -= 1; why.append("le put costano molto più delle call (skew %+.1f punti): cautela" % skew)
        elif skew < 2: score += 1; why.append("skew basso (%+.1f): poca paura di ribassi" % skew)
    label = "rialzista" if score >= 2 else "ribassista" if score <= -2 else "neutro / misto"
    return {"symbol": symbol, "spot": round(spot, 2), "asof": asof.isoformat(), "ts": doc.get("timestamp"),
            "pc_vol": pcv, "pc_oi": ratio(po, co), "cvol": cv, "pvol": pv, "coi": co, "poi": po,
            "call_premium": round(cprem), "put_premium": round(pprem),
            "expiries": per, "near_exp": near.isoformat(),
            "wall_calls": [{"k": c["k"], "oi": c["oi"]} for c in wall_c], "wall_puts": [{"k": c["k"], "oi": c["oi"]} for c in wall_p],
            "unusual": unusual, "skew": skew, "gex": round(gex / 1e6, 1), "score": score, "label": label, "why": why,
            "contracts": len(cs)}
