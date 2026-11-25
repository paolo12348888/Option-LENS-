"""OptionsLens: lettura delle opzioni di un titolo e scanner sulla tua lista. Solo libreria standard Python."""
import json, os, re, time, threading, urllib.request, urllib.error, urllib.parse, random, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import opt

HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = os.environ.get("APP_PASSWORD", "")
DEMO = os.environ.get("DEMO_DATA") == "1"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
SYMBOL = re.compile(r"^[A-Za-z0-9.\-^]{1,10}$")
INDEXES = {"SPX", "NDX", "VIX", "RUT", "DJX", "XSP"}
DEFAULT_LIST = ["QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO", "JPM",
                "BAC", "XOM", "PLTR", "COIN", "SMCI", "MU"]
CACHE, CLOCK = {}, threading.Lock()
HITS, HLOCK = {}, threading.Lock()


def cboe_url(sym):
    s = sym.upper().replace("-", "")
    return "https://cdn.cboe.com/api/global/delayed_quotes/options/%s.json" % (("_" + s) if s in INDEXES else s)


def demo_doc(sym):
    rnd = random.Random(sum(map(ord, sym))); spot = 50 + rnd.random() * 400; o = []
    today = datetime.date.today()
    for dte in (2, 9, 16, 30, 58):
        e = today + datetime.timedelta(days=dte)
        for i in range(-12, 13):
            k = round(spot * (1 + i * 0.02))
            for t in "CP":
                intr = max(0, spot - k) if t == "C" else max(0, k - spot)
                mid = intr + spot * 0.02 * (1 + dte / 20) ** 0.5 * max(0.1, 1 - abs(i) / 14)
                o.append({"option": "%s%s%s%08d" % (sym, e.strftime("%y%m%d"), t, k * 1000), "bid": mid * .98, "ask": mid * 1.02,
                          "iv": 0.25 + abs(i) * 0.01 + (0.02 if t == "P" else 0), "open_interest": int(rnd.random() * 4000),
                          "volume": int(rnd.random() * 1500 * (3 if rnd.random() < .03 else 1)),
                          "delta": max(-1, min(1, (0.5 - i * 0.07) if t == "C" else (-0.5 - i * 0.07))), "gamma": 0.01, "last_trade_price": mid})
    return {"timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "data": {"current_price": spot, "options": o}}


def get_doc(sym):
    if DEMO:
        return demo_doc(sym)
    last = "errore sconosciuto"
    for i in range(2):
        try:
            req = urllib.request.Request(cboe_url(sym), headers={
                "User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.cboe.com/", "Origin": "https://www.cboe.com"})
            return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ValueError("%s: nessuna catena di opzioni trovata (il titolo ha opzioni quotate negli USA? risposta %d)." % (sym, e.code))
            last = "la fonte ha risposto %d" % e.code
        except Exception as e:
            last = "connessione non riuscita (%s)" % type(e).__name__
        time.sleep(1.5)
    raise ValueError("Dati CBOE non disponibili ora: %s. Riprova tra poco." % last)


def analyse_symbol(sym):
    sym = sym.strip().upper()
    if not SYMBOL.match(sym):
        raise ValueError("Simbolo non valido.")
    with CLOCK:
        hit = CACHE.get(sym)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
    res = opt.analyse(get_doc(sym), sym)
    res["fetched"] = int(time.time())
    with CLOCK:
        if len(CACHE) > 300:
            CACHE.clear()
        CACHE[sym] = (time.time(), res)
    return res


def analyse_doc(sym, doc):
    sym = sym.strip().upper()
    if not SYMBOL.match(sym):
        raise ValueError("Simbolo non valido.")
    if not isinstance(doc, dict):
        raise ValueError("Dati non validi.")
    res = opt.analyse(doc, sym)
    res["fetched"] = int(time.time())
    return res


# ---------- Alpaca: prezzo pre-market / after-hours (chiavi nelle variabili d'ambiente di Render) ----------
ALPACA_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")          # "iex" = gratuito; "sip" solo con abbonamento
EXT_CACHE = {}


def et_offset_hours(dt_utc):
    """Fuso di New York (-5 inverno, -4 estate) senza dipendere da tzdata."""
    y = dt_utc.year
    d = datetime.date(y, 3, 8)
    while d.weekday() != 6: d += datetime.timedelta(days=1)          # seconda domenica di marzo
    start = datetime.datetime(y, 3, d.day, 7, tzinfo=datetime.timezone.utc)    # 2:00 EST = 7:00 UTC
    e = datetime.date(y, 11, 1)
    while e.weekday() != 6: e += datetime.timedelta(days=1)          # prima domenica di novembre
    end = datetime.datetime(y, 11, e.day, 6, tzinfo=datetime.timezone.utc)     # 2:00 EDT = 6:00 UTC
    return -4 if start <= dt_utc < end else -5


def parse_alpaca(snap):
    """Da uno snapshot Alpaca ricava prezzo fuori orario, chiusura di riferimento e fase."""
    lt = (snap or {}).get("latestTrade") or {}
    if not lt.get("p") or not lt.get("t"):
        raise ValueError("Alpaca non ha trovato scambi recenti per questo titolo.")
    t = datetime.datetime.fromisoformat(lt["t"].replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    et = t + datetime.timedelta(hours=et_offset_hours(t))
    mins = et.hour * 60 + et.minute
    phase = "pre-market" if mins < 570 else ("sessione regolare" if mins < 960 else "after-hours")
    db, pdb = snap.get("dailyBar") or {}, snap.get("prevDailyBar") or {}
    ref, ref_day = None, None
    if db.get("c") and db.get("t"):
        dbt = datetime.datetime.fromisoformat(db["t"].replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
        dbd = (dbt + datetime.timedelta(hours=et_offset_hours(dbt))).date()
        if dbd < et.date() or (dbd == et.date() and phase == "after-hours"):
            ref, ref_day = db["c"], dbd.isoformat()       # ultima chiusura già avvenuta
    if ref is None and pdb.get("c"):
        ref = pdb["c"]
        pt = datetime.datetime.fromisoformat(pdb["t"].replace("Z", "+00:00")).astimezone(datetime.timezone.utc) if pdb.get("t") else None
        ref_day = (pt + datetime.timedelta(hours=et_offset_hours(pt))).date().isoformat() if pt else None
    if ref is None and db.get("c"):
        ref, ref_day = db["c"], et.date().isoformat()
    return {"ok": True, "price": float(lt["p"]), "t": int(t.timestamp()), "et": et.strftime("%H:%M"), "phase": phase,
            "ref": float(ref) if ref else None, "ref_day": ref_day, "feed": ALPACA_FEED}


def ext_quote(sym):
    sym = sym.strip().upper()
    if not re.match(r"^[A-Z.\-]{1,10}$", sym):
        raise ValueError("Simbolo non valido.")
    if DEMO:
        return {"ok": True, "price": 148.67, "t": int(time.time()), "et": "06:09", "phase": "pre-market", "ref": 155.38, "ref_day": "demo", "feed": "demo"}
    if not (ALPACA_ID and ALPACA_SECRET):
        raise ValueError("Alpaca non è configurato sul server.")
    now = time.time(); c = EXT_CACHE.get(sym)
    if c and now - c[0] < 20: return c[1]
    url = "https://data.alpaca.markets/v2/stocks/%s/snapshot?feed=%s" % (urllib.parse.quote(sym), urllib.parse.quote(ALPACA_FEED))
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": ALPACA_ID, "APCA-API-SECRET-KEY": ALPACA_SECRET, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            snap = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403): raise ValueError("Alpaca ha rifiutato le chiavi (controlla ID e secret su Render).")
        if e.code == 429: raise ValueError("Alpaca: troppe richieste, riprova tra poco.")
        raise ValueError("Alpaca ha risposto %s." % e.code)
    except Exception:
        raise ValueError("Alpaca non raggiungibile.")
    res = parse_alpaca(snap)
    EXT_CACHE[sym] = (now, res)
    if len(EXT_CACHE) > 500: EXT_CACHE.clear()
    return res


def too_many(ip):
    now = time.time()
    with HLOCK:
        h = [t for t in HITS.get(ip, []) if now - t < 60]; h.append(now); HITS[ip] = h
        if len(HITS) > 5000: HITS.clear()
        return len(h) > 90


class H(BaseHTTPRequestHandler):
    server_version = "OptionsLens"

    def log_message(self, fmt, *a): pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)

    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def do_POST(self):
        """Modalità telefono: la pagina scarica la catena dalla CBOE col tuo telefono e la manda qui per l'analisi."""
        path, _, qs = self.path.partition("?")
        if path != "/api/analyse":
            return self.send(404, json.dumps({"error": "non trovato"}))
        if PASSWORD and self.headers.get("X-Pass", "") != PASSWORD:
            return self.send(401, json.dumps({"error": "Password errata."}))
        if too_many(self.ip()):
            return self.send(429, json.dumps({"error": "Troppe richieste: aspetta un minuto."}))
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 30_000_000:
                raise ValueError("Catena troppo grande per questo server (oltre 30 MB).")
            m = re.search(r"(?:^|&)symbol=([^&]*)", qs)
            doc = json.loads(self.rfile.read(n).decode("utf-8"))
            self.send(200, json.dumps(analyse_doc(urllib.parse.unquote(m.group(1)) if m else "", doc)))
        except ValueError as e:
            self.send(400, json.dumps({"error": str(e)}))
        except Exception as e:
            self.send(500, json.dumps({"error": "Errore interno: %s" % type(e).__name__}))

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        static = {"/manifest.webmanifest": ("application/manifest+json", "manifest.webmanifest"), "/sw.js": ("text/javascript", "sw.js"),
                  "/icon-192.png": ("image/png", "icon-192.png"), "/icon-512.png": ("image/png", "icon-512.png")}
        if path in static:
            with open(os.path.join(HERE, static[path][1]), "rb") as f:
                return self.send(200, f.read(), static[path][0])
        if path == "/health":
            return self.send(200, "ok", "text/plain")
        if path == "/api/meta":
            return self.send(200, json.dumps({"needs_password": bool(PASSWORD), "list": DEFAULT_LIST, "demo": DEMO, "ext": bool(DEMO or (ALPACA_ID and ALPACA_SECRET))}))
        if path == "/api/chain":
            if PASSWORD and self.headers.get("X-Pass", "") != PASSWORD:
                return self.send(401, json.dumps({"error": "Password errata."}))
            if too_many(self.ip()):
                return self.send(429, json.dumps({"error": "Troppe richieste: aspetta un minuto."}))
            m = re.search(r"(?:^|&)symbol=([^&]*)", qs)
            try:
                return self.send(200, json.dumps(analyse_symbol(urllib.parse.unquote(m.group(1)) if m else "")))
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}))
            except Exception as e:
                return self.send(500, json.dumps({"error": "Errore interno: %s" % type(e).__name__}))
        if path == "/api/ext":
            if PASSWORD and self.headers.get("X-Pass", "") != PASSWORD:
                return self.send(401, json.dumps({"error": "Password errata."}))
            if too_many(self.ip()):
                return self.send(429, json.dumps({"error": "Troppe richieste: aspetta un minuto."}))
            m = re.search(r"(?:^|&)symbol=([^&]*)", qs)
            try:
                return self.send(200, json.dumps(ext_quote(urllib.parse.unquote(m.group(1)) if m else "")))
            except ValueError as e:
                return self.send(200, json.dumps({"ok": False, "error": str(e)}))
            except Exception as e:
                return self.send(200, json.dumps({"ok": False, "error": "Errore interno: %s" % type(e).__name__}))
        self.send(404, json.dumps({"error": "non trovato"}))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print("OptionsLens in ascolto sulla porta", port, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
