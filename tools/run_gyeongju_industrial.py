"""Build and verify synthetic mixed traffic on the Gyeongju industrial corridor.

Example: python tools/run_gyeongju_industrial.py --out results/runs/industrial-01
All scenarios are simulation assumptions, not measured Gyeongju traffic.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import verify_gyeongju_corridor as verify

NETWORK = ROOT / "network/gyeongju_industrial/industrial.net.xml"
PROFILES = ("balanced", "northbound_peak", "southbound_peak")
TYPES = {
    "car": {"vClass": "passenger", "length": "5", "accel": "2.6", "color": "0.2,0.6,1"},
    "truck": {"vClass": "truck", "length": "12", "accel": "1.3", "color": "1,0.5,0.1"},
    "bus": {"vClass": "bus", "length": "12", "accel": "1.3", "color": "1,0.85,0.1"},
}


def generate_mixed_demand(catalog, order, profile, duration, seed, output):
    verify.require(profile in PROFILES, "Unknown demand profile")
    verify.require(duration > len(catalog) * 2, "Duration must exceed OD coverage interval")
    route_rng, type_rng = random.Random(seed), random.Random(seed + 1)
    peak_order = order if profile == "northbound_peak" else list(reversed(order))
    weights = [12 if profile != "balanced" and r["signals"] == peak_order else 1 for r in catalog]
    # Cover every physical OD once, then inject a background demand and a
    # bounded busy period. Separate RNGs keep vehicle types independent of OD.
    trips = [(i * 2, route) for i, route in enumerate(catalog)]
    depart = float(len(catalog) * 2)
    while depart < duration:
        trips.append((round(depart, 3), route_rng.choices(catalog, weights=weights, k=1)[0]))
        in_peak = profile != "balanced" and duration * 0.35 <= depart < duration * 0.75
        depart += 0.8 if in_peak else 1.5
    routes = ET.Element("routes")
    for name, attributes in TYPES.items():
        ET.SubElement(routes, "vType", {"id": name, **attributes, "sigma": "0.5", "decel": "4.5", "maxSpeed": "13.89"})
    for route in catalog:
        ET.SubElement(routes, "route", {"id": route["id"], "edges": " ".join(route["edges"])})
    schedule = []
    for index, (depart, route) in enumerate(trips):
        kind = type_rng.choices(list(TYPES), weights=[80, 12, 8], k=1)[0]
        vehicle = {"id": f"{profile}_{index:04d}", "type": kind, "route": route["id"],
                   "depart": str(depart), "departLane": "best", "departSpeed": "0"}
        ET.SubElement(routes, "vehicle", vehicle)
        schedule.append({"id": vehicle["id"], "type": kind, "depart_s": depart, "route": route["id"]})
    ET.indent(routes)
    ET.ElementTree(routes).write(output / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    verify.write_json(output / "schedule.json", {
        "synthetic": True, "profile": profile, "seed": seed, "requested_duration_s": duration,
        "last_departure_s": schedule[-1]["depart_s"], "coverage_vehicle_count": len(catalog),
        "type_sampling_weights": {"car": 80, "truck": 12, "bus": 8},
        "actual_type_counts": {kind: sum(v["type"] == kind for v in schedule) for kind in TYPES},
        "busy_window_s": None if profile == "balanced" else [duration * 0.35, duration * 0.75],
        "background_interval_s": 1.5, "busy_interval_s": None if profile == "balanced" else 0.8,
        "route_weights": {r["id"]: w for r, w in zip(catalog, weights)}, "vehicles": schedule,
        "limitations": "Synthetic mix; buses have no stops, trucks have no delivery stops; no pedestrians or parking demand.",
    })
    return schedule


def write_gui_config(network, output, seed=42):
    config = ET.Element("configuration")
    inputs = ET.SubElement(config, "input")
    ET.SubElement(inputs, "net-file", value=str(network.resolve()))
    ET.SubElement(inputs, "route-files", value="demand.rou.xml")
    random_number = ET.SubElement(config, "random_number")
    ET.SubElement(random_number, "seed", value=str(seed))
    gui = ET.SubElement(config, "gui_only")
    ET.SubElement(gui, "delay", value="100")
    ET.ElementTree(config).write(output / "view.sumocfg", encoding="utf-8", xml_declaration=True)


def run(args):
    network = args.net.resolve()
    expected = json.loads((network.parent / "contract.json").read_text(encoding="utf-8"))
    xml, movements = verify.inspect_network(network, {j["id"] for j in expected["junctions"]})
    contract = verify.inspect_contract(network, xml)
    order = verify.corridor_order(contract)
    catalog = verify.route_catalog(network, contract)
    net = verify.sumolib.net.readNet(str(network))
    for route in catalog:
        for kind in TYPES.values():
            verify.require(all(net.getEdge(edge).allows(kind["vClass"]) for edge in route["edges"]), "Route excludes a vehicle class")
    verify.require(args.duration > len(catalog) * 2 and args.drain > 0, "Invalid duration/drain")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = {"status": "RUNNING", "synthetic": True, "seed": args.seed, "duration_s": args.duration,
               "drain_limit_s": args.drain, "routes": len(catalog), "movements": len(movements),
               "network_sha256": verify.digest(network), "contract_sha256": verify.digest(network.parent / "contract.json"),
               "runner_sha256": verify.digest(__file__), "verifier_sha256": verify.digest(verify.__file__),
               "subset_sha256": verify.digest(network.parent / "source" / Path(contract["source_osm"]["subset"]).name),
               "sumo_version": subprocess.check_output([verify.sumolib.checkBinary("sumo"), "--version"], text=True).splitlines()[0]}
    verify.write_json(output / "routes.json", catalog)
    try:
        if not args.generate_only:
            summary["probes_passed"] = verify.probe_movements(network, movements, output / "probes")
        reports = {}
        for profile in PROFILES:
            destination = output / profile
            destination.mkdir()
            schedule = generate_mixed_demand(catalog, order, profile, args.duration, args.seed, destination)
            write_gui_config(network, destination, args.seed)
            verify.write_json(destination / "input_hashes.json", {"network_sha256": verify.digest(network),
                              "demand_sha256": verify.digest(destination / "demand.rou.xml"),
                              "schedule_sha256": verify.digest(destination / "schedule.json")})
            if not args.generate_only:
                report = verify.run_demand(network, destination, schedule, args.seed, args.drain)
                expected_routes = {r["id"]: r["signals"] for r in catalog}
                for vehicle in schedule:
                    verify.require(report["signal_traces"][vehicle["id"]] == expected_routes[vehicle["route"]],
                                   f"Signal traversal mismatch: {vehicle['id']}")
                reports[profile] = {k: v for k, v in report.items() if k not in {"signal_traces", "phase_transitions"}}
        summary["profiles"] = reports
        summary["status"] = "GENERATED_NOT_VERIFIED" if args.generate_only else "PASS"
    except Exception as error:
        summary.update(status="FAIL", error=str(error))
        raise
    finally:
        verify.write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", type=Path, default=NETWORK)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--drain", type=int, default=3600,
                        help="Maximum clearance time after the last requested departure (seconds)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--generate-only", action="store_true", help="Only prepare input files; never reports PASS")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
