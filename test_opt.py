import unittest, opt, random, math, datetime

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
        r = opt.analyse(chain(), "XYZ", today=datetime.date(2026, 11, 21))
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


class Stress(unittest.TestCase):
    def sim(self, **k):
        p = dict(spot=100.0, shares=1000, iv=0.35, shock=-0.2, iv_jump=0.15, hedge="bps", reinvest=0.5, dte=45, crash_days=5)
        p.update(k); return opt.simulate_crash_rebalance(**p)

    def test_put_call_parity(self):
        S, K, T, iv, r = 100.0, 95.0, 0.25, 0.3, 0.04
        c = opt.bs_price(S, K, T, iv, r, "C"); p = opt.bs_price(S, K, T, iv, r, "P")
        self.assertAlmostEqual(c - p, S - K * math.exp(-r * T), places=8)
        self.assertEqual(opt.bs_price(90, 100, 0, 0.3, 0, "P"), 10.0)

    def test_no_hedge_pnl_exact(self):
        s = self.sim(hedge="none")["steps"]
        self.assertAlmostEqual(s[1]["net_pnl"], 1000 * 100 * -0.2, places=2)
        self.assertEqual(s[2]["new_shares"], 1000)

    def test_zero_shock_hedge_costs_only(self):
        s = self.sim(shock=0.0, iv_jump=0.0, crash_days=0)["steps"]
        self.assertAlmostEqual(s[1]["hedge_pnl"], 0.0, places=6)
        s = self.sim(shock=0.0, iv_jump=0.0, slip=0.03)["steps"]
        self.assertLess(s[1]["hedge_pnl"], 0)

    def test_put_spread_gain_capped(self):
        r = self.sim(shock=-0.9, iv_jump=1.0)
        width = (95.0 - 80.0) * r["inputs"]["contracts"] * 100
        self.assertLessEqual(r["steps"][1]["hedge_pnl"], width + 1e-6)
        self.assertGreater(r["steps"][1]["hedge_pnl"], 0)

    def test_equity_identity(self):
        for h in opt.HEDGES:
            s = self.sim(hedge=h)["steps"]
            self.assertAlmostEqual(s[2]["equity"], 100000 + s[1]["asset_pnl"] + s[1]["hedge_pnl"], places=1)

    def test_rebalance_buys_more_shares_and_lowers_cost(self):
        s = self.sim()["steps"]
        self.assertGreater(s[2]["new_shares"], 1000); self.assertLess(s[2]["avg_cost"], 100.0)
        self.assertLess(s[2]["recovery_price"], 100.0)
        self.assertEqual(self.sim(reinvest=0.0)["steps"][2]["new_shares"], 1000)

    def test_backspread_has_loss_zone(self):
        r = self.sim(hedge="backspread")
        self.assertLess(r["worst_hedge_pnl"], 0); self.assertLess(r["worst_hedge_shock"], 0)
        self.assertGreater(r["curve"][-1]["hedge_pnl"], 0)

    def test_validation(self):
        for bad in (dict(shock=-0.95), dict(shock=0.1), dict(hedge="xyz"), dict(iv=0), dict(reinvest=1.5), dict(spot=-1), dict(long_pct=0.7, short_pct=0.9)):
            with self.assertRaises(ValueError): self.sim(**bad)

    def test_from_chain_data(self):
        r = opt.analyse(chain(), "XYZ"); e = next(x for x in r["expiries"] if x["atm_iv"] and x["dte"] >= 20)
        s = opt.simulate_crash_rebalance(r["spot"], 500, e["atm_iv"] / 100.0, -0.15, dte=e["dte"], crash_days=3)
        self.assertEqual(s["inputs"]["contracts"], 5); self.assertEqual(len(s["curve"]), 21)

if __name__ == "__main__":
    unittest.main()
