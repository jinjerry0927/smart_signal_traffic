"""Industrial corridor acceptance guardrails and demand replay checks."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_gyeongju_industrial as industrial
from tools import verify_gyeongju_corridor as verify


class IndustrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = industrial.NETWORK
        expected = json.loads((cls.network.parent / "contract.json").read_text(encoding="utf-8"))
        xml, cls.movements = verify.inspect_network(cls.network, {j["id"] for j in expected["junctions"]})
        cls.xml = xml
        cls.contract = verify.inspect_contract(cls.network, xml)
        cls.catalog = verify.route_catalog(cls.network, cls.contract)
        cls.order = verify.corridor_order(cls.contract)

    def test_physical_boundaries_and_routes(self):
        self.assertEqual(len(self.contract["external_boundaries"]), 8)
        self.assertEqual(len(self.catalog), 56)
        self.assertEqual(self.order, ["gj_yonggang", "gj_geunhwa", "gj_seungsam"])

    def test_missing_boundary_is_rejected(self):
        missing = copy.deepcopy(self.contract)
        missing["external_boundaries"].pop()
        with self.assertRaisesRegex(AssertionError, "Unlisted external boundary"):
            verify.route_catalog(self.network, missing)

    def test_long_vehicles_do_not_share_opposing_green(self):
        # This corridor's short internal waiting areas cannot hold a 12 m
        # truck. The chosen plan serves one approach without permissive turns.
        for program in self.xml.findall("tlLogic"):
            approaches = {int(m["linkIndex"]): m["from"] for m in self.movements if m["tl"] == program.get("id")}
            for phase in program.findall("phase")[::3]:
                state = phase.get("state")
                self.assertLessEqual(set(state), {"r", "G"})
                self.assertEqual(len({approaches[i] for i, signal in enumerate(state) if signal == "G"}), 1)
            lane_patterns = {}
            for movement in self.movements:
                if movement["tl"] != program.get("id"):
                    continue
                lane = (movement["from"], movement["fromLane"])
                pattern = tuple(p.get("state")[int(movement["linkIndex"])] for p in program.findall("phase"))
                self.assertEqual(lane_patterns.setdefault(lane, pattern), pattern)

    def test_mixed_demand_replays_and_peak_changes_direction(self):
        with tempfile.TemporaryDirectory() as temporary:
            outputs = [Path(temporary) / str(i) for i in range(4)]
            schedules = []
            for output, profile in zip(outputs, ("balanced", "balanced", "northbound_peak", "southbound_peak")):
                output.mkdir()
                schedules.append(industrial.generate_mixed_demand(self.catalog, self.order, profile, 600, 42, output))
            self.assertEqual((outputs[0] / "demand.rou.xml").read_bytes(), (outputs[1] / "demand.rou.xml").read_bytes())
            for schedule in schedules:
                self.assertEqual({v["type"] for v in schedule}, {"car", "truck", "bus"})
                self.assertEqual({v["route"] for v in schedule[:56]}, {r["id"] for r in self.catalog})
                self.assertTrue(all(0 <= v["depart_s"] < 600 for v in schedule))
            routes = {r["id"]: r for r in self.catalog}
            count = lambda schedule, order: sum(routes[v["route"]]["signals"] == order for v in schedule[56:])
            self.assertGreater(count(schedules[2], self.order), count(schedules[3], self.order))
            self.assertGreater(count(schedules[3], self.order[::-1]), count(schedules[2], self.order[::-1]))
            self.assertGreater(len(schedules[2]), len(schedules[0]))

    def test_invalid_duration_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(AssertionError, "Duration must exceed"):
                industrial.generate_mixed_demand(self.catalog, self.order, "balanced", 50, 42, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
