"""Static acceptance checks for the selected map layout; no simulation steps."""
import itertools
import json
import math
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import sumolib

NETWORK = Path(__file__).resolve().parents[1] / "network/gyeongju_simple"


class MapLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.xml = ET.parse(NETWORK / "simple.net.xml").getroot()
        cls.net = sumolib.net.readNet(str(NETWORK / "simple.net.xml"))
        cls.contract = json.loads((NETWORK / "contract.json").read_text(encoding="utf-8"))
        cls.links = [c for c in cls.xml.findall("connection") if c.get("tl")]

    def test_three_junctions_with_smooth_roads(self):
        ids = ["gj_yonggang", "gj_geunhwa", "gj_seungsam"]
        self.assertEqual({t.getID() for t in self.net.getTrafficLights()}, set(ids))
        self.assertEqual(len(self.net.getEdges()), 20)
        self.assertEqual(len(self.contract["external_boundaries"]), 8)
        positions = [self.net.getNode(j).getCoord() for j in ids]
        for a, b in zip(positions, positions[1:]):
            self.assertGreater(b[0], a[0])
            self.assertGreater(b[1], a[1])
        for edge in self.net.getEdges():
            self.assertGreater(edge.getLength(), 80)
            for lane in edge.getLanes():
                points = lane.getShape()
                self.assertGreater(len(points), 8, edge.getID())
                for a, b, c in zip(points, points[1:], points[2:]):
                    u, v = (b[0]-a[0], b[1]-a[1]), (c[0]-b[0], c[1]-b[1])
                    self.assertGreater(math.hypot(*u), .01)
                    self.assertGreater(math.hypot(*v), .01)
                    angle = abs(math.degrees(math.atan2(u[0]*v[1]-u[1]*v[0], u[0]*v[0]+u[1]*v[1])))
                    self.assertLess(angle, 8, f"Sharp corner on {lane.getID()}")

    def test_lane_arrows_and_usable_lanes(self):
        self.assertEqual(sum(e.getLaneNumber() for e in self.net.getEdges()), 30)
        self.assertEqual(len(self.links), 42)
        actual = {(c.get("tl"), int(c.get("linkIndex"))): c for c in self.links}
        for m in self.contract["controlled_movements"]:
            c = actual[(m["junction"], m["link_index"])]
            self.assertEqual(c.get("dir"), {"left": "l", "straight": "s", "right": "r"}[m["turn"]])
            self.assertEqual((c.get("from"), c.get("to")), (m["from_edge"], m["to_edge"]))
        for edge in self.net.getEdges():
            for lane in edge.getLanes():
                for vclass in ("passenger", "bus", "truck"):
                    self.assertTrue(lane.allows(vclass))
                if edge.getToNode().getType() == "traffic_light":
                    turns = {c.getDirection() for c in lane.getOutgoing()}
                    self.assertTrue(turns, lane.getID())
                    self.assertNotIn("t", turns)
                    if lane.getIndex() == 0:
                        self.assertIn("r", turns)
                    if lane.getIndex() == edge.getLaneNumber()-1:
                        self.assertIn("l", turns)
                if edge.getFromNode().getType() == "traffic_light":
                    self.assertTrue(any(c.get("to") == edge.getID() and int(c.get("toLane")) == lane.getIndex()
                                        for c in self.links), f"Unreachable outgoing lane {lane.getID()}")

    def test_signal_clearance_and_conflicts(self):
        for program in self.xml.findall("tlLogic"):
            tls = program.get("id")
            links = sorted((c for c in self.links if c.get("tl") == tls), key=lambda c: int(c.get("linkIndex")))
            phases = program.findall("phase")
            self.assertEqual(len(phases), 12)
            self.assertEqual(sum(float(p.get("duration")) for p in phases), 120)
            controlled = {}
            for lane, target, idx in self.net.getTLS(tls).getConnections():
                controlled[idx] = next(c.getJunctionIndex() for c in lane.getOutgoing() if c.getToLane() == target)
            node = self.net.getNode(tls)
            patterns = {}
            for c in links:
                idx = int(c.get("linkIndex"))
                pattern = tuple(p.get("state")[idx] for p in phases)
                self.assertEqual(pattern.count("G"), 1)
                key = (c.get("from"), c.get("fromLane"))
                self.assertEqual(patterns.setdefault(key, pattern), pattern, "Shared-lane turns have different signals")
            for green, yellow, red in zip(phases[::3], phases[1::3], phases[2::3]):
                state = green.get("state")
                self.assertEqual(len(state), len(links))
                self.assertLessEqual(set(state), {"G", "r"})
                self.assertEqual(yellow.get("state"), state.replace("G", "y"))
                self.assertEqual(red.get("state"), "r" * len(links))
                self.assertEqual(float(yellow.get("duration")), 3)
                self.assertEqual(float(red.get("duration")), 2)
                active = [i for i, s in enumerate(state) if s == "G"]
                self.assertEqual(len({links[i].get("from") for i in active}), 1)
                for a, b in itertools.combinations(active, 2):
                    self.assertFalse(node.areFoes(controlled[a], controlled[b]) or node.areFoes(controlled[b], controlled[a]))

    def test_all_demo_routes_connect_for_each_vehicle_class(self):
        demand = ET.parse(NETWORK / "demo.rou.xml").getroot()
        routes = demand.findall("route")
        self.assertEqual(len(routes), 56)
        self.assertEqual(len(demand.findall("vehicle")), 56)
        for route in routes:
            edges = [self.net.getEdge(e) for e in route.get("edges").split()]
            for source, target in zip(edges, edges[1:]):
                self.assertIn(target, source.getOutgoing())
            for vclass in ("passenger", "bus", "truck"):
                self.assertTrue(self.net.getShortestPath(edges[0], edges[-1], vClass=vclass)[0])


if __name__ == "__main__":
    unittest.main()
