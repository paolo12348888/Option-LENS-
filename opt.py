"""Analisi di una catena di opzioni (dati CBOE ritardati). Solo libreria standard."""
import re, datetime, math

OCC = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")


def f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _date_of(x):
    try:
        return datetime.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


# Giorni di borsa chiusa NYSE 2026-2027 (oltre ai fine settimana)
HOLIDAYS = {datetime.date(*d) for d in [
    (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 4, 3), (2026, 5, 25), (2026, 6, 19), (2026, 7, 3), (2026, 9, 7), (2026, 11, 26), (2026, 12, 25),
    (2027, 1, 1), (2027, 1, 18), (2027, 2, 15), (2027, 3, 26), (2027, 5, 31), (2027, 6, 18), (2027, 7, 5), (2027, 9, 6), (2027, 11, 25), (2027, 12, 24)]}


def last_session(d):
    """Ultima giornata di borsa aperta fino al giorno d compreso."""
    while d.weekday() >= 5 or d in HOLIDAYS:
        d -= datetime.timedelta(days=1)
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
                    "delta": f(o.get("delta")), "gamma": f(o.get("gamma")), "sym": o.get("option"),
                    "ltt": _date_of(o.get("last_trade_time"))})
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


def bs_gamma(S, K, iv, T):
    """Gamma di Black-Scholes (tassi e dividendi a zero)."""
    if S <= 0 or K <= 0 or iv <= 0 or T <= 0:
        return 0.0
    sd = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * sd)


GRID = [0.85 + 0.005 * i for i in range(61)]          # da -15% a +15% rispetto al prezzo, passo 0,5%


def gex_curve(cs, spot, asof):
    """Gamma netta (milioni di $ per 1%) se il prezzo valesse spot*m, per ogni m della griglia.
    Ipotesi classica: call +, put - (chi fa mercato è dall'altra parte dei clienti)."""
    out = [0.0] * len(GRID)
    for c in cs:
        if c["oi"] <= 0 or c["iv"] <= 0 or abs(c["k"] / spot - 1) > 0.3:   # gli strike lontani pesano quasi zero
            continue
        T = max((c["exp"] - asof).days, 0.5) / 365.0
        sign = 1 if c["type"] == "C" else -1
        for i, m in enumerate(GRID):
            S = spot * m
            out[i] += sign * bs_gamma(S, c["k"], c["iv"], T) * c["oi"] * 100 * S * S * 0.01 / 1e6
    return out


def zero_gamma(curve, spot):
    """Prezzo in cui la gamma netta cambia segno (il più vicino al prezzo attuale), oppure None."""
    best = None
    for i in range(len(curve) - 1):
        a, b = curve[i], curve[i + 1]
        if a == 0 or (a < 0) != (b < 0):
            x = GRID[i] if a == 0 else GRID[i] + (GRID[i + 1] - GRID[i]) * (0 - a) / (b - a)
            p = spot * x
            if best is None or abs(p - spot) < abs(best - spot):
                best = p
    return None if best is None else round(best, 2)


def gex_of(c, spot):
    """Esposizione gamma di un contratto in milioni di $ per un movimento dell'1% (gamma del feed x OI x 100 x prezzo^2 x 1%)."""
    return c["gamma"] * c["oi"] * 100 * spot * spot * 0.01 / 1e6


def walls(cs, spot):
    """Muri per GEX (gamma x open interest) e strike con più open interest. Si sommano i contratti con lo stesso strike (utile
    quando si uniscono più scadenze). Lo strike al denaro è compreso."""
    agg = {}
    for c in cs:
        if c["oi"] <= 0:
            continue
        a = agg.setdefault((c["type"], c["k"]), [0, 0.0])
        a[0] += c["oi"]; a[1] += gex_of(c, spot)
    ks = sorted({k for _, k in agg})
    lo = [k for k in ks if k <= spot]; hi = [k for k in ks if k >= spot]
    alo = lo[-1] if lo else spot; ahi = hi[0] if hi else spot
    calls = {k: v for (t, k), v in agg.items() if t == "C" and k >= alo}
    puts = {k: v for (t, k), v in agg.items() if t == "P" and k <= ahi}

    def top(d, i):
        return max(d, key=lambda k: d[k][i]) if d else None
    cw, pw, co, po = top(calls, 1), top(puts, 1), top(calls, 0), top(puts, 0)
    return {"call_wall": cw, "put_wall": pw,
            "call_oi_top": None if co is None else {"k": co, "oi": calls[co][0]},
            "put_oi_top": None if po is None else {"k": po, "oi": puts[po][0]}}


def ratio(a, b):
    return None if b == 0 else round(a / b, 2)


def analyse(doc, symbol, today=None):
    spot, ts_date, cs = parse_chain(doc)
    # La data del file CBOE è quella in cui è stato generato, non della seduta: nel fine settimana indica sabato/domenica.
    # La seduta vera è l'ultimo scambio registrato nei contratti; se manca, l'ultimo giorno di borsa aperta.
    ltts = [c["ltt"] for c in cs if c["ltt"] and c["vol"] > 0 and c["ltt"] <= ts_date]
    trade_date = max(ltts) if ltts else last_session(ts_date)
    # I giorni alla scadenza si contano da oggi (se i dati sono di venerdì e siamo domenica, mancano due giorni in meno).
    asof = max(trade_date, today or datetime.date.today())
    if spot <= 0 or not cs:
        raise ValueError("La catena di opzioni di %s è vuota o non valida." % symbol)
    cs = [c for c in cs if c["exp"] >= asof]
    cv = sum(c["vol"] for c in cs if c["type"] == "C"); pv = sum(c["vol"] for c in cs if c["type"] == "P")
    co = sum(c["oi"] for c in cs if c["type"] == "C"); po = sum(c["oi"] for c in cs if c["type"] == "P")
    cprem = sum(c["vol"] * c["mid"] * 100 for c in cs if c["type"] == "C")
    pprem = sum(c["vol"] * c["mid"] * 100 for c in cs if c["type"] == "P")
    exps = sorted({c["exp"] for c in cs})
    per = []
    curves = []
    for e in exps[:10]:
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
        w = walls(ec, spot)
        cwall, pwall = w["call_wall"], w["put_wall"]
        curve = gex_curve(ec, spot, asof)
        gnet = round(sum((1 if c['type'] == 'C' else -1) * gex_of(c, spot) for c in ec), 1)
        curves.append(curve)
        sd = None
        if atm_iv:
            sd = spot * (atm_iv / 100.0) * math.sqrt(max((e - asof).days, 0.5) / 365.0)   # 1 deviazione standard in $
        win = walls([c for c in ec if abs(c["k"] - spot) <= sd], spot) if sd else None   # muri solo entro ±1σ
        per.append({"exp": e.isoformat(), "dte": (e - asof).days, "call_wall": cwall, "put_wall": pwall,
                    "call_wall_in": win["call_wall"] if win else None, "put_wall_in": win["put_wall"] if win else None,
                    "call_oi_top": w["call_oi_top"], "put_oi_top": w["put_oi_top"],
                    "sd1": None if sd is None else round(sd, 2),
                    "sd1_up": None if sd is None else round(spot + sd, 2), "sd1_dn": None if sd is None else round(spot - sd, 2),
                    "sd2_up": None if sd is None else round(spot + 2 * sd, 2), "sd2_dn": None if sd is None else round(spot - 2 * sd, 2),
                    "zero_gamma": zero_gamma(curve, spot),
                    "gex_net": gnet,
                    "cvol": sum(c["vol"] for c in ec if c["type"] == "C"), "pvol": sum(c["vol"] for c in ec if c["type"] == "P"),
                    "coi": sum(c["oi"] for c in ec if c["type"] == "C"), "poi": sum(c["oi"] for c in ec if c["type"] == "P"),
                    "atm_iv": atm_iv, "move_pct": move, "max_pain": max_pain(ec)})
    tot = [c for c in cs if c["exp"] in set(exps[:10])]
    tw = walls(tot, spot)
    totals = {"call_wall": tw["call_wall"], "put_wall": tw["put_wall"], "call_oi_top": tw["call_oi_top"], "put_oi_top": tw["put_oi_top"], "max_pain": max_pain(tot), "zero_gamma": zero_gamma([sum(v) for v in zip(*curves)], spot) if curves else None}
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
    # dati per strike (grafici): solo gli strike entro il 25% dal prezzo, prime 6 scadenze
    by_exp = {}
    for e in exps[:10]:
        rows = {}
        for c in cs:
            if c["exp"] != e or abs(c["k"] / spot - 1) > 0.25:
                continue
            r = rows.setdefault(c["k"], {"k": c["k"], "coi": 0, "poi": 0, "cvol": 0, "pvol": 0, "civ": None, "piv": None, "cg": 0.0, "pg": 0.0})
            if c["type"] == "C":
                r["coi"], r["cvol"], r["civ"] = c["oi"], c["vol"], (round(c["iv"] * 100, 1) if c["iv"] > 0 else None)
                r["cg"] = round(gex_of(c, spot), 3)
            else:
                r["poi"], r["pvol"], r["piv"] = c["oi"], c["vol"], (round(c["iv"] * 100, 1) if c["iv"] > 0 else None)
                r["pg"] = round(-gex_of(c, spot), 3)
        by_exp[e.isoformat()] = [rows[k] for k in sorted(rows)]
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
    # --- Modalità intraday: solo gli strike vicini al prezzo, scadenze entro 7 giorni (max 5) ---
    intraday = None
    iv_front = next((p["atm_iv"] for p in per if p["atm_iv"]), None)
    daily = spot * (iv_front / 100.0) * math.sqrt(1 / 252.0) if iv_front else spot * 0.01     # 1 deviazione standard di un giorno di borsa
    wpct = min(max(2 * daily / spot, 0.02), 0.10)                                               # finestra: ±2σ giornaliere, tra 2% e 10%
    lo_k, hi_k = spot * (1 - wpct), spot * (1 + wpct)
    irows = []
    for p in ([x for x in per if x["dte"] <= 7][:5] or per[:1]):
        e = datetime.date.fromisoformat(p["exp"])
        ec = [c for c in cs if c["exp"] == e and lo_k <= c["k"] <= hi_k]
        if not ec:
            continue
        w = walls(ec, spot)
        irows.append({"exp": p["exp"], "dte": p["dte"], "call_wall": w["call_wall"], "put_wall": w["put_wall"],
                      "call_oi_top": w["call_oi_top"], "put_oi_top": w["put_oi_top"],
                      "gex_net": round(sum((1 if c["type"] == "C" else -1) * gex_of(c, spot) for c in ec), 1),
                      "zero_gamma": zero_gamma(gex_curve(ec, spot, asof), spot), "max_pain_full": p["max_pain"]})
    if irows:
        intraday = {"window_pct": round(wpct * 100, 1), "lo": round(lo_k, 2), "hi": round(hi_k, 2), "daily_sd": round(daily, 2), "iv": iv_front,
                    "sd1_dn": round(spot - daily, 2), "sd1_up": round(spot + daily, 2), "sd2_dn": round(spot - 2 * daily, 2), "sd2_up": round(spot + 2 * daily, 2),
                    "rows": irows}
    return {"symbol": symbol, "spot": round(spot, 2), "asof": ts_date.isoformat(), "trade_date": trade_date.isoformat(), "ref": asof.isoformat(), "ts": doc.get("timestamp"),
            "pc_vol": pcv, "pc_oi": ratio(po, co), "cvol": cv, "pvol": pv, "coi": co, "poi": po,
            "call_premium": round(cprem), "put_premium": round(pprem),
            "expiries": per, "totals": totals, "intraday": intraday, "near_exp": near.isoformat(),
            "wall_calls": [{"k": c["k"], "oi": c["oi"]} for c in wall_c], "wall_puts": [{"k": c["k"], "oi": c["oi"]} for c in wall_p],
            "unusual": unusual, "unusual_count": len(un), "by_exp": by_exp, "skew": skew, "gex": round(gex / 1e6, 1), "score": score, "label": label, "why": why,
            "contracts": len(cs)}
