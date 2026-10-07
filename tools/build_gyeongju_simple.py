"""Build a schematic corridor and check its structure; never run traffic.

Edit layout.json for geometry/lane counts. Existing generated outputs require
--overwrite. The original OSM-based industrial corridor is not modified.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import sumolib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "network/gyeongju_simple"
ARMS = "NESW"
GENERATED = ("nodes.nod.xml", "roads.edg.xml", "turns.con.xml", "signals.tll.xml",
             "simple.net.xml", "contract.json", "demo.rou.xml", "demo.sumocfg",
             "view.xml", "labels.add.xml", "open_sumo.cmd")


def xml_write(path, root):
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def smooth_shape(points, radius, step=5):
    """Round intermediate corners, keeping endpoints and approach tangents.

    Radius is the trim distance along each segment, not a surveyed turn radius.
    Curves add shape points only: no extra junctions, edges or signal actions.
    """
    if radius < 0 or step <= 0:
        raise ValueError("curve_radius_m must be nonnegative and curve_step_m positive")
    if radius == 0:
        return points
    rounded = [points[0]]
    for a, b, c in zip(points, points[1:], points[2:]):
        before, after = math.dist(a, b), math.dist(b, c)
        if min(before, after) == 0:
            raise ValueError("Consecutive road shape points must be distinct")
        trim = min(radius, before * .4, after * .4)
        start = tuple(b[k] + (a[k] - b[k]) * trim / before for k in (0, 1))
        end = tuple(b[k] + (c[k] - b[k]) * trim / after for k in (0, 1))
        rounded.append(start)
        samples = max(8, math.ceil(2 * trim / step))
        for i in range(1, samples + 1):
            t = i / samples
            rounded.append(tuple((1 - t) ** 2 * start[k] + 2 * t * (1 - t) * b[k] + t ** 2 * end[k] for k in (0, 1)))
    rounded.append(points[-1])
    return [(round(x, 3), round(y, 3)) for x, y in rounded]


def build(layout_path, output, overwrite=False):
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    if not overwrite and any((output / name).exists() for name in GENERATED):
        raise SystemExit("Generated files already exist; use another --out or explicit --overwrite")
    output.mkdir(parents=True, exist_ok=True)
    if layout_path.resolve() != (output / "layout.json").resolve():
        (output / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    junctions = {j["id"]: j for j in layout["junctions"]}
    arms = {j: {} for j in junctions}
    nodes, edges, connections, programs = [ET.Element(tag) for tag in ("nodes", "edges", "connections", "tlLogics")]
    for j in junctions.values():
        ET.SubElement(nodes, "node", id=j["id"], x=str(j["x"]), y=str(j["y"]), type="traffic_light", radius="12")
    boundaries, movements = [], []
    for road in layout["roads"]:
        a, b, count = road["a"], road["b"], road["lanes"]
        if not isinstance(count, int) or count < 1:
            raise ValueError("lanes must be a positive integer")
        end = (junctions[b]["x"], junctions[b]["y"]) if b in junctions else road["end_xy"]
        if b not in junctions:
            ET.SubElement(nodes, "node", id=b, x=str(end[0]), y=str(end[1]), type="priority", fringe="outer")
        shape = [(junctions[a]["x"], junctions[a]["y"]), *road.get("via", []), end]
        shape = smooth_shape(shape, layout.get("curve_radius_m", 0), layout.get("curve_step_m", 5))
        forward, reverse = road["id"] + "_out", road["id"] + "_in"
        for eid, start, finish, points in ((forward, a, b, shape), (reverse, b, a, shape[::-1])):
            ET.SubElement(edges, "edge", {"id": eid, "from": start, "to": finish, "numLanes": str(count),
                          "speed": str(layout["speed_mps"]), "width": str(layout["lane_width_m"]),
                          "spreadType": "right", "allow": "passenger bus truck", "name": road["name"],
                          "shape": " ".join(f"{x},{y}" for x, y in points)})
        arms[a][road["arm_a"]] = {"in": reverse, "out": forward, "lanes": count}
        if b in junctions:
            arms[b][road["arm_b"]] = {"in": forward, "out": reverse, "lanes": count}
        else:
            boundaries.append({"id": b, "junction": a, "arm": road["arm_a"],
                               "fringe_nodes": {"inbound": b, "outbound": b},
                               "edges": {"inbound": reverse, "outbound": forward}})
    for tls, approaches in arms.items():
        if set(approaches) != set(ARMS):
            raise ValueError(f"Expected four explicit arms at {tls}")
        groups = []
        index = 0
        for position, arm in enumerate(ARMS):
            incoming = approaches[arm]
            group = []
            for turn, shift in (("right", -1), ("straight", 2), ("left", 1)):
                target_arm = ARMS[(position + shift) % 4]
                outgoing = approaches[target_arm]
                if turn == "straight":
                    # Avoid merging two protected streams into one receiving
                    # lane inside the junction; the inner lane serves left.
                    lane_pairs = [(i, i) for i in range(min(incoming["lanes"], outgoing["lanes"]))]
                elif turn == "right":
                    lane_pairs = [(0, 0)]
                else:
                    lane_pairs = [(incoming["lanes"] - 1, outgoing["lanes"] - 1)]
                for source_lane, target_lane in lane_pairs:
                    ET.SubElement(connections, "connection", {"from": incoming["in"], "to": outgoing["out"],
                                  "fromLane": str(source_lane), "toLane": str(target_lane), "tl": tls, "linkIndex": str(index)})
                    movements.append({"junction": tls, "arm": arm, "turn": turn, "link_index": index,
                                      "from_edge": incoming["in"], "from_lane": source_lane,
                                      "to_edge": outgoing["out"], "to_lane": target_lane})
                    group.append(index)
                    index += 1
            groups.append(group)
        program = ET.SubElement(programs, "tlLogic", id=tls, type="static", programID="0", offset="0")
        for arm, group in zip(ARMS, groups):
            for duration, active in ((layout["green_s"], "G"), (layout["yellow_s"], "y"), (layout["all_red_s"], "r")):
                state = "".join(active if i in group else "r" for i in range(index))
                stage = {"G": "green", "y": "yellow", "r": "all_red"}[active]
                ET.SubElement(program, "phase", duration=str(duration), state=state, name=f"{arm}_{stage}")
    for filename, tree in zip(GENERATED[:4], (nodes, edges, connections, programs)):
        xml_write(output / filename, tree)
    network = output / "simple.net.xml"
    subprocess.run([sumolib.checkBinary("netconvert"), "--node-files", str(output / GENERATED[0]),
                    "--edge-files", str(output / GENERATED[1]), "--connection-files", str(output / GENERATED[2]),
                    "--tllogic-files", str(output / GENERATED[3]), "--output-file", str(network),
                    "--offset.disable-normalization", "true", "--no-turnarounds", "true",
                    "--junctions.corner-detail", "8", "--junctions.internal-link-detail", "8"], check=True)
    # Drop generated timestamp/path comments for reproducible file contents.
    xml_write(network, ET.parse(network).getroot())
    net = sumolib.net.readNet(str(network))
    assert {t.getID() for t in net.getTrafficLights()} == set(junctions)
    assert len(net.getEdges()) == 20, "Unexpected short connector or extra road"
    assert all(edge.getLength() > 80 for edge in net.getEdges())
    imported = ET.parse(network).getroot()
    actual = {(c.get("tl"), int(c.get("linkIndex")), c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane")))
              for c in imported.findall("connection") if c.get("tl")}
    declared = {(m["junction"], m["link_index"], m["from_edge"], m["from_lane"], m["to_edge"], m["to_lane"]) for m in movements}
    assert actual == declared, "Lane connections changed during conversion"
    directions = {(c.get("tl"), int(c.get("linkIndex"))): c.get("dir")
                  for c in imported.findall("connection") if c.get("tl")}
    assert all(directions[(m["junction"], m["link_index"])] == {"right": "r", "straight": "s", "left": "l"}[m["turn"]]
               for m in movements), "Geometry no longer matches the declared turn directions"
    for program in programs:
        junction = net.getNode(program.get("id"))
        controlled = {}
        for lane, outgoing, link_index in net.getTLS(program.get("id")).getConnections():
            controlled[link_index] = next(c for c in lane.getOutgoing() if c.getToLane() == outgoing).getJunctionIndex()
        generated_program = imported.find(f"tlLogic[@id='{program.get('id')}']")
        assert [(p.get("duration"), p.get("state")) for p in program] == [(p.get("duration"), p.get("state")) for p in generated_program]
        for phase in list(program)[::3]:
            green = [controlled[i] for i, s in enumerate(phase.get("state")) if s == "G"]
            assert not any(junction.areFoes(a, b) or junction.areFoes(b, a) for a, b in itertools.combinations(green, 2)), "Conflicting greens"
    routes = ET.Element("routes")
    for kind, vclass, length, color in (("car", "passenger", 5, "44,134,218"), ("bus", "bus", 12, "242,188,56"), ("truck", "truck", 12, "232,132,68")):
        ET.SubElement(routes, "vType", id=kind, vClass=vclass, length=str(length), maxSpeed=str(layout["speed_mps"]), color=color)
    od_routes = []
    for entry, exit_ in itertools.permutations(boundaries, 2):
        start, end = net.getEdge(entry["edges"]["inbound"]), net.getEdge(exit_["edges"]["outbound"])
        path, _ = net.getShortestPath(start, end, vClass="passenger")
        assert path, f"Unreachable route: {entry['id']} -> {exit_['id']}"
        for vehicle_class in ("bus", "truck"):
            assert net.getShortestPath(start, end, vClass=vehicle_class)[0], "Vehicle class disconnected"
        rid = f"od_{len(od_routes):02d}"
        road_ids = [e.getID() for e in path]
        ET.SubElement(routes, "route", id=rid, edges=" ".join(road_ids))
        od_routes.append({"id": rid, "from": entry["id"], "to": exit_["id"], "edges": road_ids})
    for i, route in enumerate(od_routes):
        kind = "car" if i % 10 < 8 else "bus" if i % 10 == 8 else "truck"
        ET.SubElement(routes, "vehicle", id=f"example_{i:03d}", type=kind, route=route["id"], depart=str(i * 4), departLane="best", departSpeed="0")
    xml_write(output / "demo.rou.xml", routes)
    contract = {"schema_version": 2, "network": "simple.net.xml", "coordinate_system": "local_schematic_metres",
                "geometry_is_surveyed": False, "junctions": layout["junctions"], "corridor_order": list(junctions),
                "external_boundaries": boundaries, "controlled_movements": movements, "routes": od_routes,
                "signals": {p.get("id"): {"offset_s": 0, "phases": [dict(q.attrib) for q in p]} for p in programs},
                "validation": "Static lane/route/signal checks only; no traffic simulation or model training.",
                "assumptions": ["Lane counts retain the earlier model assumptions, not verified current field counts.",
                                "Geometry is schematic; do not project it as real geographic coordinates.",
                                "No U-turns, pedestrians, intermediate alleys or bus stops. Timings are examples."]}
    (output / "contract.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    labels = ET.Element("additional")
    for j in junctions.values():
        ET.SubElement(labels, "poi", id="_".join(j["label"].split()), x=str(j["x"] + 45), y=str(j["y"] + 35), color="52,68,87", layer="5")
    xml_write(output / "labels.add.xml", labels)
    view = ET.Element("viewsettings")
    scheme = ET.SubElement(view, "scheme", name="map_clean_corridor")
    ET.SubElement(scheme, "background", backgroundColor="247,248,246", showGrid="0")
    display_edges = ET.SubElement(scheme, "edges", laneEdgeMode="0", laneShowBorders="1", showLinkDecals="1", showLinkRules="1", hideConnectors="1", edgeName_show="0", streetName_show="0")
    colors = ET.SubElement(display_edges, "colorScheme", name="uniform")
    ET.SubElement(colors, "entry", color="103,116,132", name="road")
    ET.SubElement(scheme, "pois", poiName_show="1", poiName_size="32", poiName_color="52,68,87")
    ET.SubElement(scheme, "vehicles", vehicleQuality="2", vehicle_exaggeration="1.5", vehicle_constantSize="1")
    xml_write(output / "view.xml", view)
    config = ET.Element("configuration")
    inputs = ET.SubElement(config, "input")
    for key, value in (("net-file", "simple.net.xml"), ("route-files", "demo.rou.xml"), ("additional-files", "labels.add.xml")):
        ET.SubElement(inputs, key, value=value)
    gui = ET.SubElement(config, "gui_only")
    ET.SubElement(gui, "gui-settings-file", value="view.xml")
    ET.SubElement(gui, "delay", value="100")
    xml_write(output / "demo.sumocfg", config)
    (output / "open_sumo.cmd").write_text('@echo off\nsumo-gui -c "%~dp0demo.sumocfg"\nif errorlevel 1 pause\n', encoding="ascii")
    print(f"STATIC CHECK PASS: {len(junctions)} signals, {len(net.getEdges())} roads, {len(movements)} lane turns, {len(od_routes)} OD routes. No simulation run.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=DEFAULT / "layout.json")
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    build(args.layout.resolve(), args.out.resolve(), args.overwrite)
