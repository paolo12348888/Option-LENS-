import unittest, opt, random

def chain(spot=100.0):
    random.seed(3); o = []
    for exp, dte in (("261204", 13), ("261218", 27), ("270115", 55)):
        for k in range(80, 125, 5):
            for t in "CP":
                intr = max(0, spot - k) if t == "C" else max(0, k - spot)
                mid = intr + 3.0
                d = (0.5 + (spot - k) / 60) if t == "C" else (-0.5 + (spot - k) / 60)
                o.append({"option": "XYZ%s%s%08d" % (exp, t, k * 1000), "bid": mid - .1, "ask": mid + .1, "iv": 0.3 + abs(k - spot) / 400 + (0.03 if t == "P" else 0),
                          "open_interest": 1000 + (k == 110 and t == "C") * 5000 + (k == 90 and t == "P") * 4000,
                          "volume": 50 + (k == 105 and t == "C") * 3000, "delta": max(-1, min(1, d)), "gamma": 0.02, "last_trade_price": mid})
    return {"timestamp": "2026-11-21 16:15:00", "data": {"current_price": spot, "options": o}}

class T(unittest.TestCase):
    def test_parse(self):
        s, a, cs = opt.parse_chain(chain())
        self.assertEqual(s, 100.0); self.assertEqual(str(a), "2026-11-21"); self.assertEqual(len(cs), 3 * 9 * 2)
        c = [x for x in cs if x["type"] == "C" and x["k"] == 110][0]
        self.assertEqual(c["oi"], 6000)

    def test_occ_weird_root(self):
        m = opt.OCC.match("SPXW261204C05000000"); self.assertEqual(m.group(1), "SPXW"); self.assertEqual(int(m.group(4)) / 1000, 5000.0)

    def test_analyse(self):
        r = opt.analyse(chain(), "XYZ")
        self.assertEqual(r["wall_calls"][0]["k"], 110.0); self.assertEqual(r["wall_puts"][0]["k"], 90.0)
        self.assertTrue(r["unusual"] and r["unusual"][0]["k"] == 105.0 and r["unusual"][0]["type"] == "C")
        self.assertEqual(r["expiries"][0]["dte"], 13)
        self.assertAlmostEqual(r["expiries"][0]["move_pct"], 6.0, places=1)   # straddle 3+3 su 100... ATM 100: mid 3 + 3
        self.assertGreater(r["call_premium"], r["put_premium"])
        self.assertIn(r["label"], ("rialzista", "neutro / misto", "ribassista"))

    def test_strikes(self):
        r = opt.analyse(chain(), "XYZ")
        rows = r["by_exp"]["2026-12-18"]
        self.assertTrue(all(abs(x["k"] / 100 - 1) <= 0.25 for x in rows))
        k110 = [x for x in rows if x["k"] == 110][0]
        self.assertEqual(k110["coi"], 6000); self.assertEqual(k110["poi"], 1000)
        self.assertGreaterEqual(r["unusual_count"], 1)

    def test_empty(self):
        with self.assertRaises(ValueError): opt.analyse({"data": {"current_price": 10, "options": []}}, "X")

if __name__ == "__main__":
    unittest.main()
