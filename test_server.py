import os, unittest
os.environ["DEMO_DATA"] = "1"
import server


class S(unittest.TestCase):
    def test_symbol_only_with_shares_and_date(self):
        r = server.stress_query("symbol=NVDA&shares=100&date=2025-01-15&shock=-0.2&hedge=bps")
        self.assertEqual(r["context"]["symbol"], "NVDA"); self.assertIn("iv_source", r["context"])
        self.assertIsNotNone(r["position"]); self.assertEqual(r["inputs"]["shares"], 100)
        self.assertGreaterEqual(r["inputs"]["dte"], 7)

    def test_usd_invested_with_cost(self):
        r = server.stress_query("symbol=AAPL&usd=10000&cost=50&shock=-0.1&hedge=none")
        self.assertAlmostEqual(r["inputs"]["shares"], 200.0); self.assertAlmostEqual(r["position"]["invested"], 10000.0)

    def test_manual_spot_iv(self):
        r = server.stress_query("spot=100&iv=0.3&shares=500&shock=-0.2")
        self.assertEqual(r["context"], {}); self.assertIsNone(r["position"])

    def test_errors(self):
        for q in ("symbol=NVDA&shock=-0.2", "symbol=@@&shares=1&shock=-0.2", "symbol=NVDA&shares=1&shock=-0.2&date=2030-01-01",
                  "symbol=NVDA&shares=1&shock=-0.2&date=ieri", "shares=1&shock=-0.2"):
            with self.assertRaises(ValueError): server.stress_query(q)


if __name__ == "__main__":
    unittest.main()
