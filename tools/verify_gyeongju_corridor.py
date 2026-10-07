"""Reproducible synthetic demand and SUMO acceptance checks for SET2.

Run from any directory. The output directory must not already exist.
This checks a simulation model, not field signal safety or traffic performance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import sumolib
import traci

ROOT = Path(__file__).resolve().parents[1]
TLS_IDS = {"gj_south", "gj_center", "gj_north"}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def inspect_network(path, tls_ids=None):
    xml = ET.parse(path).getroot()
    net = sumolib.net.readNet(str(path))
    programs = xml.findall("tlLogic")
    require({p.get("id") for p in programs} == (TLS_IDS if tls_ids is None else set(tls_ids)), "Expected exactly three canonical traffic lights")
    require(len(programs) == 3, "Each traffic light must have one static program")
    movements = [dict(c.attrib) for c in xml.findall("connection") if c.get("tl")]
    require(all(m["dir"] != "t" for m in movements), "U-turn is outside this model")
    for program in programs:
        require(program.get("type") == "static", "SET2 requires a static program")
        controlled = {}
        for incoming, outgoing, index in net.getTLS(program.get("id")).getConnections():
            controlled[index] = next(c for c in incoming.getOutgoing()
                                     if c.getToLane() == outgoing and c.getTLSID() == program.get("id"))
        links = {int(m["linkIndex"]) for m in movements if m["tl"] == program.get("id")}
        phases = program.findall("phase")
        require(len(phases) % 3 == 0, "Expected green/yellow/all-red triples")
        require(links == set(range(max(links) + 1)), "Controlled link indices have gaps")
        served = set()
        for index in range(0, len(phases), 3):
            green, yellow, red = phases[index:index + 3]
            state = green.get("state")
            require(len(state) == len(links), "Signal state length mismatch")
            require(set(state) <= set("rGg") and any(c in "Gg" for c in state), "Invalid green phase")
            require(float(green.get("duration")) == 30, "SET2 green duration must be 30 seconds")
            expected = "".join("y" if c in "Gg" else "r" for c in state)
            require(yellow.get("state") == expected and float(yellow.get("duration")) == 3, "Missing yellow clearance")
            require(red.get("state") == "r" * len(state) and float(red.get("duration")) == 2, "Missing all-red clearance")
            protected = [i for i, char in enumerate(state) if char == "G"]
            for first in protected:
                for second in protected:
                    if first >= second:
                        continue
                    a, b = controlled[first], controlled[second]
                    require(not a.getJunction().areFoes(a.getJunctionIndex(), b.getJunctionIndex()),
                            f"Conflicting protected greens at {program.get('id')}: {first}, {second}")
            served.update(i for i, c in enumerate(state) if c in "Gg")
        require(served == links, "A permitted movement is never served")
    return xml, movements


def inspect_contract(path, xml):
    contract_path = path.parent / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    net = sumolib.net.readNet(str(path))
    require(len(contract["junctions"]) == 3 and {j["id"] for j in contract["junctions"]} == {p.get("id") for p in xml.findall("tlLogic")}, "Contract junction IDs mismatch")
    for junction in contract["junctions"]:
        program = xml.find(f"tlLogic[@id='{junction['id']}']")
        actual = [(float(p.get("duration")), p.get("state")) for p in program.findall("phase")]
        declared = [(p["duration"], p["state"]) for p in contract["tls_programs"][junction["id"]]["phases"]]
        require(actual == declared, "Contract phase data mismatch")
        require(float(program.get("offset", "0")) == contract["tls_programs"][junction["id"]].get("offset_s", 0),
                "Contract signal offset mismatch")
        actual_movements = {(int(c.get("linkIndex")), c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane")), c.get("dir"))
                            for c in xml.findall("connection") if c.get("tl") == junction["id"]}
        declared_movements = {(c["link_index"], c["from_edge"], c["from_lane"], c["to_edge"], c["to_lane"], c["direction"])
                              for c in contract["tls_programs"][junction["id"]]["controlled_movements"]}
        require(actual_movements == declared_movements, "Contract controlled movements mismatch")
    for boundary in contract["external_boundaries"]:
        for direction in ("inbound", "outbound"):
            edge = net.getEdge(boundary["edges"][direction])
            node = edge.getFromNode() if direction == "inbound" else edge.getToNode()
            require(node.getType() == "dead_end" and node.getID() == boundary["fringe_nodes"][direction],
                    f"Boundary mismatch: {boundary['id']}")
    return contract


def corridor_order(contract):
    order = contract.get("corridor_order", [j["id"] for j in sorted(contract["junctions"], key=lambda j: j["lat"])])
    require(len(order) == 3 and set(order) == {j["id"] for j in contract["junctions"]}, "Invalid corridor order")
    return order


def route_catalog(path, contract):
    net = sumolib.net.readNet(str(path))
    boundaries = contract["external_boundaries"]
    require(len(boundaries) >= 2 and len({b["id"] for b in boundaries}) == len(boundaries), "Invalid physical external boundaries")
    for direction in ("inbound", "outbound"):
        require(len({b["edges"][direction] for b in boundaries}) == len(boundaries), "Duplicate boundary edges")
        actual = {e.getID() for e in net.getEdges()
                  if (e.getFromNode() if direction == "inbound" else e.getToNode()).getType() == "dead_end"}
        require(actual == {b["edges"][direction] for b in boundaries}, "Unlisted external boundary")
    catalog = []
    for entry in boundaries:
        for exit_ in boundaries:
            if entry["id"] == exit_["id"]:
                continue
            source = net.getEdge(entry["edges"]["inbound"])
            target = net.getEdge(exit_["edges"]["outbound"])
            edges, cost = net.getShortestPath(source, target, vClass="passenger")
            require(edges, f"Disconnected boundary route: {entry['id']} -> {exit_['id']}")
            signals = []
            for first, second in zip(edges, edges[1:]):
                for connection in first.getConnections(second):
                    tls = connection.getTLSID()
                    if tls and (not signals or signals[-1] != tls):
                        signals.append(tls)
            catalog.append({"id": f"od_{len(catalog):02d}", "edges": [e.getID() for e in edges],
                            "entry": entry["id"], "exit": exit_["id"], "length_m": cost, "signals": signals})
    order = corridor_order(contract)
    require(any(r["signals"] == order for r in catalog), "No northbound corridor route")
    require(any(r["signals"] == list(reversed(order)) for r in catalog), "No southbound corridor route")
    return catalog


def generate_demand(catalog, profile, duration, seed, output, order=None):
    require(duration > len(catalog) * 2, "Duration must exceed the mandatory OD coverage interval")
    rng = random.Random(seed)
    order = ["gj_south", "gj_center", "gj_north"] if order is None else order
    weights = [4 if profile == "northbound_peak" and r["signals"] == order else 1 for r in catalog]
    interval = {"light": 5, "balanced": 2, "northbound_peak": 2}[profile]
    # All OD paths appear once, followed by seeded demand. These are fixture loads.
    departures = [(i * 2, r) for i, r in enumerate(catalog)]
    start = len(catalog) * 2
    for depart in range(start, max(start, duration), interval):
        departures.append((depart, rng.choices(catalog, weights=weights, k=1)[0]))
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", {"id": "car", "vClass": "passenger", "sigma": "0.5"})
    for route in catalog:
        ET.SubElement(routes, "route", {"id": route["id"], "edges": " ".join(route["edges"])})
    schedule = []
    for index, (depart, route) in enumerate(departures):
        vehicle = {"id": f"{profile}_{index:04d}", "type": "car", "route": route["id"],
                   "depart": str(depart), "departLane": "best", "departSpeed": "0"}
        ET.SubElement(routes, "vehicle", vehicle)
        schedule.append({"id": vehicle["id"], "depart_s": depart, "route": route["id"]})
    ET.indent(routes)
    ET.ElementTree(routes).write(output / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json(output / "schedule.json", {"synthetic": True, "profile": profile, "seed": seed,
               "requested_duration_s": duration, "last_departure_s": departures[-1][0],
               "coverage_vehicle_count": len(catalog), "weighted_vehicle_count": len(schedule) - len(catalog),
               "interval_s_after_coverage": interval, "route_weights": dict(zip([r["id"] for r in catalog], weights)),
               "vehicles": schedule})
    return schedule


def open_simulation(network, output, seed, routes=None, step="1"):
    command = [sumolib.checkBinary("sumo"), "--net-file", str(network), "--seed", str(seed),
               "--step-length", step, "--no-step-log", "true", "--duration-log.disable", "true",
               "--collision.check-junctions", "true", "--time-to-teleport", "300",
               "--tripinfo-output", str(output / "tripinfo.xml"), "--tripinfo-output.write-unfinished", "true",
               "--error-log", str(output / "sumo-errors.log")]
    if routes:
        command += ["--route-files", str(routes)]
    write_json(output / "command.json", command)
    with (output / "sumo.stdout.log").open("w", encoding="utf-8") as stdout:
        traci.start(command, label="set2", stdout=stdout)
    connection = traci.getConnection("set2")
    try:
        contract = json.loads((network.parent / "contract.json").read_text(encoding="utf-8"))
        for junction in contract["junctions"]:
            # SUMO performs its own projection; no optional pyproj dependency.
            lon, lat = connection.simulation.convertGeo(*connection.junction.getPosition(junction["id"]))
            require(abs(lon - junction["lon"]) < 0.0002 and abs(lat - junction["lat"]) < 0.0002,
                    f"Contract coordinates disagree with network: {junction['id']}")
    except Exception:
        connection.close()
        raise
    return connection


def run_demand(network, output, schedule, seed, drain):
    connection = open_simulation(network, output, seed, output / "demand.rou.xml")
    departed, arrived, teleports, collisions = set(), set(), [], []
    traces = {v["id"]: [] for v in schedule}
    tls_ids = set(connection.trafficlight.getIDList())
    transitions = {tls: [{"time": 0, "phase": connection.trafficlight.getPhase(tls)}] for tls in sorted(tls_ids)}
    approaches = {tls: sorted({lane.rsplit("_", 1)[0] for lane in connection.trafficlight.getControlledLanes(tls)}) for tls in transitions}
    samples = []
    max_time = max(v["depart_s"] for v in schedule) + drain
    try:
        while connection.simulation.getMinExpectedNumber() and connection.simulation.getTime() < max_time:
            connection.simulationStep()
            departed.update(connection.simulation.getDepartedIDList())
            arrived.update(connection.simulation.getArrivedIDList())
            teleports.extend(connection.simulation.getStartingTeleportIDList())
            collisions.extend(connection.simulation.getCollidingVehiclesIDList())
            for tls, history in transitions.items():
                phase = connection.trafficlight.getPhase(tls)
                if phase != history[-1]["phase"]:
                    history.append({"time": connection.simulation.getTime(), "phase": phase})
            for vehicle in connection.vehicle.getIDList():
                road = connection.vehicle.getRoadID(vehicle)
                if road.startswith(":"):
                    junction = road[1:].rsplit("_", 1)[0]
                    if junction in tls_ids and (not traces[vehicle] or traces[vehicle][-1] != junction):
                        traces[vehicle].append(junction)
            sample = {"time_s": connection.simulation.getTime(), "active": connection.vehicle.getIDCount(),
                      "departed": len(departed), "arrived": len(arrived),
                      "pending": connection.simulation.getMinExpectedNumber() - connection.vehicle.getIDCount()}
            for tls, edges in approaches.items():
                sample[tls + "_halting"] = sum(connection.edge.getLastStepHaltingNumber(edge) for edge in edges)
            samples.append(sample)
        simulated = connection.simulation.getTime()
        active = list(connection.vehicle.getIDList())
    finally:
        connection.close()
    trips = ET.parse(output / "tripinfo.xml").getroot().findall("tripinfo")
    completed = [t for t in trips if float(t.get("arrival")) >= 0]
    planned = {v["id"] for v in schedule}
    report = {"planned": len(planned), "departed": len(departed), "arrived": len(arrived),
              "not_departed": sorted(planned - departed), "not_arrived": sorted(planned - arrived),
              "active": active, "teleports": teleports, "collisions": collisions,
              "completed_tripinfo": len(completed), "simulated_s": simulated,
              "mean_completed_travel_time_s": sum(float(t.get("duration")) for t in completed) / max(1, len(completed)),
              "max_approach_halting_vehicles": {tls: max(s[tls + "_halting"] for s in samples) for tls in tls_ids},
              "signal_traces": traces, "phase_transitions": transitions}
    with (output / "timeseries.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(samples[0]))
        writer.writeheader()
        writer.writerows(samples)
    write_json(output / "metrics.json", report)
    require(departed == planned == arrived, f"Unfinished demand: see {output / 'metrics.json'}")
    require(len(completed) == len(planned) and {t.get("id") for t in completed} == planned,
            "Tripinfo arrival ID/count mismatch")
    require(not teleports and not collisions, "Unexpected teleport/collision")
    require(not (output / "sumo-errors.log").read_text(encoding="utf-8").strip(), "SUMO reported warnings/errors; inspect sumo-errors.log")
    xml = ET.parse(network).getroot()
    for program in xml.findall("tlLogic"):
        phases = program.findall("phase")
        # The initial observation is before the first SUMO step. Check complete
        # intervals after that initial segment, including cyclic wrap-around.
        history = transitions[program.get("id")]
        require(len(history) > len(phases), "Run too short to observe a complete signal cycle")
        for current, following in zip(history[1:], history[2:]):
            require(following["phase"] == (current["phase"] + 1) % len(phases), "Runtime phase order mismatch")
            require(following["time"] - current["time"] == float(phases[current["phase"]].get("duration")), "Runtime phase duration mismatch")
    return report


def probe_movements(network, movements, output):
    output.mkdir()
    connection = open_simulation(network, output, 42, step="0.2")
    records = []
    net = sumolib.net.readNet(str(network))
    try:
        for index, movement in enumerate(movements):
            vehicle = f"probe_{index:03d}"
            connection.route.add(vehicle, [movement["from"], movement["to"]])
            # Probe the junction itself; full boundary routes are checked separately.
            incoming = net.getLane(movement["from"] + "_" + movement["fromLane"])
            outgoing = net.getLane(movement["to"] + "_" + movement["toLane"])
            start = max(0, incoming.getLength() - 50)
            # Joined junctions may leave sub-metre connector edges. Keep the
            # arrival point inside the actual lane and select the exact turn
            # when one source lane connects to multiple destination lanes.
            end = min(50, outgoing.getLength() / 2)
            connection.vehicle.add(vehicle, vehicle, departLane=movement["fromLane"], departSpeed="0", departPos=str(start),
                                   arrivalLane=movement["toLane"], arrivalPos=str(end))
            connection.vehicle.setLaneChangeMode(vehicle, 0)
            connection.vehicle.setMaxSpeed(vehicle, 5)
            seen, departed, arrived = [], False, False
            teleports, collisions = [], []
            deadline = connection.simulation.getTime() + 300
            while connection.simulation.getTime() < deadline:
                connection.simulationStep()
                teleports.extend(connection.simulation.getStartingTeleportIDList())
                collisions.extend(connection.simulation.getCollidingVehiclesIDList())
                departed = departed or vehicle in connection.simulation.getDepartedIDList()
                if vehicle in connection.vehicle.getIDList():
                    lane = connection.vehicle.getLaneID(vehicle)
                    if not seen or seen[-1] != lane:
                        seen.append(lane)
                if vehicle in connection.simulation.getArrivedIDList():
                    arrived = True
                    break
            record = {**movement, "vehicle": vehicle, "departed": departed, "arrived": arrived,
                      "lanes": seen, "teleports": teleports, "collisions": collisions}
            records.append(record)
            write_json(output / "movements.json", records)
            require(departed and arrived and not teleports and not collisions, f"Movement probe failed: {record}")
            require(movement["via"] in seen, f"Expected internal movement not traversed: {record}")
    finally:
        connection.close()
    require(not (output / "sumo-errors.log").read_text(encoding="utf-8").strip(), "Movement probes produced SUMO warnings/errors")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", type=Path, default=ROOT / "network/gyeongju_corridor/corridor.net.xml")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--drain", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    require(args.duration > 0 and args.drain > 0, "Duration and drain must be positive")
    network = args.net.resolve()
    expected = json.loads((network.parent / "contract.json").read_text(encoding="utf-8"))
    xml, movements = inspect_network(network, {j["id"] for j in expected["junctions"]})
    contract = inspect_contract(network, xml)
    catalog = route_catalog(network, contract)
    require(args.duration > len(catalog) * 2, f"Duration must exceed {len(catalog) * 2}s for OD coverage plus scenario demand")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    summary = {"status": "RUNNING", "network_sha256": digest(network), "synthetic": True,
               "contract_sha256": digest(network.parent / "contract.json"),
               "verifier_sha256": digest(__file__),
               "subset_sha256": digest(network.parent / "source" / Path(contract["source_osm"]["subset"]).name),
               "sumo_version": subprocess.check_output([sumolib.checkBinary("sumo"), "--version"], text=True).splitlines()[0],
               "seed": args.seed, "duration_s": args.duration, "drain_limit_s": args.drain,
               "routes": len(catalog), "movements": len(movements)}
    write_json(args.out / "routes.json", catalog)
    try:
        summary["probes_passed"] = probe_movements(network, movements, args.out / "probes")
        reports = {}
        for profile in ("light", "balanced", "northbound_peak"):
            output = args.out / profile
            output.mkdir()
            schedule = generate_demand(catalog, profile, args.duration, args.seed, output, corridor_order(contract))
            write_json(output / "input_hashes.json", {"network_sha256": digest(network),
                       "demand_sha256": digest(output / "demand.rou.xml"), "schedule_sha256": digest(output / "schedule.json")})
            report = run_demand(network, output, schedule, args.seed, args.drain)
            # Internal edge traces prove real traversal, rather than route intent alone.
            for vehicle in schedule:
                expected = next(r["signals"] for r in catalog if r["id"] == vehicle["route"])
                require(report["signal_traces"][vehicle["id"]] == expected, f"Signal traversal mismatch: {vehicle['id']}")
            reports[profile] = {k: v for k, v in report.items() if k not in {"signal_traces", "phase_transitions"}}
        summary["profiles"] = reports
        summary["status"] = "PASS"
    except Exception as error:
        summary.update(status="FAIL", error=str(error))
        raise
    finally:
        write_json(args.out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
