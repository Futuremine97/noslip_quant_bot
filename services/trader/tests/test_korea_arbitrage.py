import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import korea_arbitrage as ka  # noqa: E402

NO_COST = {
    **ka.DEFAULT_CONFIG,
    "commission_bps": 0.0, "slippage_bps": 0.0, "sell_tax_bps": 0.0,
    "min_edge_bps": 1.0, "commission_bps_overrides": {},
}


class TestSafety(unittest.TestCase):
    def test_engine_never_calls_submit_or_cancel(self):
        src = (Path(__file__).resolve().parents[1] / "korea_arbitrage.py").read_text(encoding="utf-8")
        self.assertNotIn(".submit_order(", src)
        self.assertNotIn(".cancel_order(", src)


class TestCostModel(unittest.TestCase):
    def test_sell_includes_transaction_tax(self):
        cfg = ka.load_config()
        diff = ka.leg_cost_bps(cfg, "kis", "SELL") - ka.leg_cost_bps(cfg, "kis", "BUY")
        self.assertEqual(diff, cfg["sell_tax_bps"])

    def test_provider_override(self):
        cfg = {**NO_COST, "commission_bps_overrides": {"kiwoom": 9.0}}
        self.assertEqual(ka.leg_cost_bps(cfg, "kiwoom", "BUY"), 9.0)


class TestTwoWay(unittest.TestCase):
    MATRIX = {"005930": {"a": 70000.0, "b": 71000.0}}

    def test_exact_edge_without_costs(self):
        opps = ka.scan_two_way(self.MATRIX, NO_COST)
        self.assertEqual(len(opps), 1)
        self.assertAlmostEqual(opps[0]["net_edge_bps"], 1000 / 70000 * 10000, places=1)
        self.assertEqual(opps[0]["buy"]["provider"], "a")
        self.assertEqual(opps[0]["sell"]["provider"], "b")

    def test_costs_kill_marginal_opportunity(self):
        cfg = {**NO_COST, "sell_tax_bps": 200.0}
        self.assertEqual(ka.scan_two_way(self.MATRIX, cfg), [])

    def test_single_provider_no_opportunity(self):
        self.assertEqual(ka.scan_two_way({"005930": {"a": 70000.0}}, NO_COST), [])


class TestRoutes(unittest.TestCase):
    def test_buy_sell_route_matches_two_way(self):
        matrix = {"005930": {"a": 70000.0, "b": 71000.0}}
        cfg = {**NO_COST, "routes": [{"name": "t", "legs": [
            {"provider": "a", "symbol": "005930", "side": "BUY", "ratio": 1.0},
            {"provider": "b", "symbol": "005930", "side": "SELL", "ratio": 1.0},
        ]}]}
        routes = ka.scan_routes(matrix, cfg)
        two = ka.scan_two_way(matrix, NO_COST)
        self.assertAlmostEqual(routes[0]["net_edge_bps"], two[0]["net_edge_bps"], delta=0.5)

    def test_infeasible_route_skipped(self):
        cfg = {**NO_COST, "routes": [{"name": "x", "legs": [
            {"provider": "zzz", "symbol": "999999", "side": "BUY", "ratio": 1.0},
            {"provider": "a", "symbol": "005930", "side": "SELL", "ratio": 1.0},
        ]}]}
        self.assertEqual(ka.scan_routes({"005930": {"a": 1.0}}, cfg), [])


class TestNegativeCycles(unittest.TestCase):
    def test_demo_matrix_has_cycle(self):
        matrix, _ = ka.demo_quote_matrix()
        cycles = ka.scan_negative_cycles(matrix, ka.load_config())
        self.assertTrue(cycles)
        self.assertGreater(cycles[0]["net_edge_bps"], 0)
        actions = {s.get("action") for s in cycles[0]["steps"]}
        self.assertIn("BUY", actions)
        self.assertIn("SELL", actions)

    def test_flat_matrix_has_no_cycle(self):
        matrix = {"005930": {"a": 70000.0, "b": 70000.0}}
        self.assertEqual(ka.scan_negative_cycles(matrix, ka.load_config()), [])


class TestPlanPreviewOnly(unittest.TestCase):
    def test_plan_builds_previews_with_warning(self):
        opps = ka.scan_two_way({"005930": {"a": 70000.0, "b": 71000.0}}, NO_COST)
        plan = ka.build_execution_plan(opps[0], notional_krw=1_000_000)
        self.assertIn("WARNING", plan)
        self.assertEqual(len(plan["legs"]), 2)
        for leg in plan["legs"]:
            self.assertTrue("preview" in leg or "preview_error" in leg)


if __name__ == "__main__":
    unittest.main()
