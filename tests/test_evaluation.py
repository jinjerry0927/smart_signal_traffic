"""출발 일정 동결, 지표의 단위·차량 회계, 기준선 시간 기준을 검증한다."""
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from baselines import green_phase_action, webster_timing
from evaluation_support import EpisodeMetrics, freeze_departures, file_hash


class EvaluationTests(unittest.TestCase):
    def test_frozen_departures_preserve_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, first, second = [Path(tmp) / name for name in ("source.xml", "a.xml", "b.xml")]
            source.write_text('<routes><route id="r" edges="N2C C2S"/>'
                '<flow id="f" route="r" begin="0" end="100" vehsPerHour="900"/></routes>')
            original = file_hash(source)
            self.assertEqual(freeze_departures(source, first, 12), 3)
            freeze_departures(source, second, 12)
            self.assertEqual(file_hash(first), file_hash(second))
            self.assertEqual(file_hash(source), original)
            vehicles = ET.parse(first).getroot().findall("vehicle")
            self.assertEqual([v.get("id") for v in vehicles], ["f.0", "f.1", "f.2"])
            self.assertEqual([float(v.get("depart")) for v in vehicles], [0, 4, 8])

    def test_reject_stochastic_demand(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.xml"
            path.write_text('<routes><flow id="f" probability="0.5"/></routes>')
            with self.assertRaises(ValueError):
                freeze_departures(path, Path(tmp) / "out.xml", 10)

    def test_per_second_metrics_and_completed_vehicle_mean(self):
        metrics = EpisodeMetrics()
        frames = [(["a", "b"], [], {"a": (3, 0), "b": (0, 2)}),
                  ([], ["a"], {"b": (4, 0)}), ([], [], {"b": (0, 2)})]
        for departed, arrived, vehicles in frames:
            connection = SimpleNamespace(
                simulation=SimpleNamespace(getDepartedIDList=lambda: departed,
                    getArrivedIDList=lambda: arrived, getStartingTeleportNumber=lambda: 0,
                    getCollidingVehiclesNumber=lambda: 0),
                vehicle=SimpleNamespace(getIDList=lambda: list(vehicles),
                    getWaitingTime=lambda v: vehicles[v][0], getSpeed=lambda v: vehicles[v][1]))
            metrics.sample(connection)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tripinfo.xml"
            path.write_text('<tripinfos><tripinfo id="a" arrival="8" duration="8" '
                            'waitingTime="3" timeLoss="2"/></tripinfos>')
            result = metrics.finish(path, running=1, pending=1, scheduled=3)
            self.assertAlmostEqual(result["mean_system_total_waiting_time_s"], 7 / 3)
            self.assertAlmostEqual(result["mean_queue_vehicles"], 2 / 3)
            self.assertEqual(result["arrived_vehicles"], 1)
            self.assertEqual(result["not_departed_vehicles"], 1)
            self.assertEqual(result["departed_not_arrived_vehicles"], 1)
            self.assertEqual(result["mean_completed_waiting_time_s"], 3)
            self.assertEqual(result["mean_completed_travel_time_s"], 8)

    def test_baseline_measures_green_not_wall_clock(self):
        signal = SimpleNamespace(green_phase=2, green_elapsed=0, num_green_phases=4)
        env = SimpleNamespace(traffic_signals={"center": signal}, ts_ids=["center"])
        self.assertEqual(green_phase_action(env, [30] * 4), 2)
        signal.green_elapsed = 25
        self.assertEqual(green_phase_action(env, [30] * 4), 2)
        signal.green_elapsed = 30
        self.assertEqual(green_phase_action(env, [30] * 4), 3)
        self.assertEqual(webster_timing(dict(N=0, S=0, E=0, W=0))["green"], [15] * 4)


if __name__ == "__main__":
    unittest.main()
