"""Regression checks for SET2 verification and reproducible demand."""
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import verify_gyeongju_corridor as corridor

NET = ROOT / "network/gyeongju_corridor/corridor.net.xml"


class CorridorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        xml, _ = corridor.inspect_network(NET)
        cls.contract = corridor.inspect_contract(NET, xml)
        cls.catalog = corridor.route_catalog(NET, cls.contract)

    def test_all_boundaries_connected(self):
        self.assertEqual(len(self.catalog), 42)
        self.assertEqual(len({(r["entry"], r["exit"]) for r in self.catalog}), 42)

    def test_rejects_conflicting_protected_greens(self):
        tree = ET.parse(NET)
        phases = tree.getroot().find("tlLogic").findall("phase")
        count = len(phases[0].get("state"))
        phases[0].set("state", "G" * count)
        phases[1].set("state", "y" * count)
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "invalid.net.xml"
            tree.write(invalid, encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "Conflicting protected greens"):
                corridor.inspect_network(invalid)

    def test_rejects_missing_all_red(self):
        tree = ET.parse(NET)
        tree.getroot().find("tlLogic").findall("phase")[2].set("duration", "0")
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "invalid.net.xml"
            tree.write(invalid, encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "Missing all-red"):
                corridor.inspect_network(invalid)

    def test_seed_replay_and_profile_difference(self):
        with tempfile.TemporaryDirectory() as temporary:
            outputs = [Path(temporary) / name for name in ("a", "b", "peak")]
            for output in outputs:
                output.mkdir()
            for output, profile in zip(outputs, ("balanced", "balanced", "northbound_peak")):
                corridor.generate_demand(self.catalog, profile, 180, 42, output)
            self.assertEqual((outputs[0] / "demand.rou.xml").read_bytes(), (outputs[1] / "demand.rou.xml").read_bytes())
            plans = [json.loads((output / "schedule.json").read_text(encoding="utf-8")) for output in outputs]
            self.assertNotEqual([v["route"] for v in plans[0]["vehicles"]], [v["route"] for v in plans[2]["vehicles"]])

    def test_output_preserved_and_short_duration_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with patch.object(sys, "argv", ["verify", "--out", str(output)]):
                with self.assertRaises(FileExistsError):
                    corridor.main()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            new_output = Path(temporary) / "new"
            with patch.object(sys, "argv", ["verify", "--out", str(new_output), "--duration", "60"]):
                with self.assertRaisesRegex(AssertionError, "Duration must exceed"):
                    corridor.main()
            self.assertFalse(new_output.exists())


if __name__ == "__main__":
    unittest.main()
