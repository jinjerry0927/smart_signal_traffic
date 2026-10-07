"""Build the Yonggang-Geunhwa-Seungsam SUMO corridor from pinned OSM data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "network" / "gyeongju_industrial"
SOURCE_URL = (
    "https://api.openstreetmap.org/api/0.6/map?"
    "bbox=129.215,35.862,129.234,35.882"
)
SOURCE_DATE = "2026-09-25"
SOURCE_SHA256 = "f2dfeae3893d41a6fcf70652ef045e1cb1dfb4294435acaa5b06af61cf966f27"
MAX_ARM_DISTANCE_M = 330.0

JUNCTIONS = [
    {
        "id": "gj_yonggang",
        "label": "용강네거리",
        "lat": 35.8675992,
        "lon": 129.2236595,
        "osm_ids": ["529483975", "13807673402", "13807673403"],
    },
    {
        "id": "gj_geunhwa",
        "label": "근화여고네거리",
        "lat": 35.8722779,
        "lon": 129.2257674,
        "osm_ids": ["436736928", "13807673604"],
    },
    {
        "id": "gj_seungsam",
        "label": "승삼네거리",
        "lat": 35.8769551,
        "lon": 129.2273545,
        "osm_ids": ["436736929", "13807673605"],
    },
]
ANCHORS = {junction["id"]: (junction["lat"], junction["lon"]) for junction in JUNCTIONS}

# Each way is clipped only around the listed junctions. This keeps the main
# carriageways continuous while preventing external arms from reconnecting
# outside the intended corridor.
WAY_ANCHORS = {
    # Yonggang to Geunhwa main carriageways.
    "1165014312": ("gj_yonggang", "gj_geunhwa"),
    "1435311645": ("gj_yonggang", "gj_geunhwa"),
    "550137593": ("gj_yonggang", "gj_geunhwa"),
    "1330059386": ("gj_yonggang", "gj_geunhwa"),
    # Geunhwa to Seungsam main carriageways.
    "1510438989": ("gj_geunhwa", "gj_seungsam"),
    "1171256005": ("gj_geunhwa", "gj_seungsam"),
    "1330059385": ("gj_geunhwa", "gj_seungsam"),
    # Yonggang south-west Wonhwa-ro arm.
    "1165014311": ("gj_yonggang",),
    # Yonggang south-east Saneop-ro arm and its existing slip link.
    "1435307993": ("gj_yonggang",),
    "1435311644": ("gj_yonggang",),
    "1436677989": ("gj_yonggang",),
    "550137596": ("gj_yonggang",),
    # Yonggang west Yurim-ro arm.
    "1431683203": ("gj_yonggang",),
    # Geunhwa west/east arms.
    "42410605": ("gj_geunhwa",),
    "1331526504": ("gj_geunhwa",),
    "791274407": ("gj_geunhwa",),
    "1510438990": ("gj_geunhwa",),
    # Seungsam west arm and its northbound slip link.
    "1330059384": ("gj_seungsam",),
    "1510438970": ("gj_seungsam",),
    "1510438971": ("gj_seungsam",),
    # Seungsam east arm and existing slip links.
    "550137598": ("gj_seungsam",),
    "888388950": ("gj_seungsam",),
    "891102455": ("gj_seungsam",),
    "1238003413": ("gj_seungsam",),
    "1330074578": ("gj_seungsam",),
    "1510438991": ("gj_seungsam",),
    # Seungsam north main-road arm.
    "1238003414": ("gj_seungsam",),
    "1431685342": ("gj_seungsam",),
    "1510438992": ("gj_seungsam",),
}

# Missing lane tags are explicit model inputs. Divided main carriageways use
# the two lanes tagged on adjacent carriageways; local two-way roads use one
# lane per direction; one-way slip links use one lane.
TWO_LANE_ONEWAY = {
    "550137593",
    "1165014311",
    "1165014312",
    "1171256005",
    "1238003414",
    "1330059385",
    "1330059386",
    "1431685342",
    "1435307993",
    "1435311644",
    "1435311645",
    "1436677989",
    "1510438989",
    "1510438992",
}
ONE_LANE_ONEWAY = {"550137596", "888388950", "891102455", "1510438970"}
TWO_LANE_TWOWAY = {
    "42410605",
    "550137598",
    "791274407",
    "1238003413",
    "1330059384",
    "1330074578",
    "1331526504",
    "1431683203",
    "1510438971",
    "1510438990",
    "1510438991",
}
MAIN_ROAD_WAYS = {
    "550137593",
    "1165014311",
    "1165014312",
    "1171256005",
    "1238003414",
    "1330059385",
    "1330059386",
    "1431685342",
    "1435307993",
    "1435311644",
    "1435311645",
    "1436677989",
    "1510438989",
    "1510438992",
}
OSM_EXPLICIT_LANES_2 = {
    "1165014311",
    "1165014312",
    "1431683203",
    "1435307993",
    "1435311644",
    "1436677989",
    "1510438989",
    "1510438992",
}
MISSING_MAIN_LANE_OVERRIDES = TWO_LANE_ONEWAY.difference(OSM_EXPLICIT_LANES_2)
MISSING_LOCAL_LANE_OVERRIDES = TWO_LANE_TWOWAY.difference(OSM_EXPLICIT_LANES_2)
OSM_EXPLICIT_MAXSPEED_70 = {"1431683203", "1435307993", "1435311644"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distance_m(lat: float, lon: float, anchor: tuple[float, float]) -> float:
    return math.hypot((lat - anchor[0]) * 111_111, (lon - anchor[1]) * 90_000)


def prepare_output(output_dir: Path, overwrite: bool) -> None:
    generated = [
        output_dir / "source" / "industrial_corridor.osm.xml",
        output_dir / "industrial.net.xml",
        output_dir / "contract.json",
    ]
    existing = [path for path in generated if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise SystemExit(f"refusing to overwrite generated files: {names}; use --overwrite")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "source").mkdir(parents=True, exist_ok=True)


def write_subset(source: Path, subset_path: Path) -> None:
    actual = sha256(source)
    if actual != SOURCE_SHA256:
        raise SystemExit(
            f"source SHA-256 mismatch: expected {SOURCE_SHA256}, got {actual}"
        )

    source_root = ET.parse(source).getroot()
    source_nodes = {node.attrib["id"]: node for node in source_root.findall("node")}
    source_ways = {
        way.attrib["id"]: way
        for way in source_root.findall("way")
        if way.attrib["id"] in WAY_ANCHORS
    }
    missing = set(WAY_ANCHORS).difference(source_ways)
    if missing:
        raise SystemExit(f"source is missing required OSM ways: {sorted(missing)}")

    clipped_refs: dict[str, list[str]] = {}
    synthetic_nodes: dict[str, tuple[float, float]] = {}

    def coordinates(node_id: str) -> tuple[float, float]:
        node = source_nodes[node_id]
        return float(node.attrib["lat"]), float(node.attrib["lon"])

    def way_distance(way_id: str, point: tuple[float, float]) -> float:
        return min(
            distance_m(point[0], point[1], ANCHORS[name])
            for name in WAY_ANCHORS[way_id]
        )

    def boundary_point(
        way_id: str, outside_id: str, inside_id: str
    ) -> tuple[float, float]:
        outside = coordinates(outside_id)
        inside = coordinates(inside_id)
        low, high = 0.0, 1.0
        for _ in range(50):
            ratio = (low + high) / 2
            point = (
                outside[0] + (inside[0] - outside[0]) * ratio,
                outside[1] + (inside[1] - outside[1]) * ratio,
            )
            if way_distance(way_id, point) > MAX_ARM_DISTANCE_M:
                low = ratio
            else:
                high = ratio
        return (
            outside[0] + (inside[0] - outside[0]) * high,
            outside[1] + (inside[1] - outside[1]) * high,
        )

    for way_id, way in source_ways.items():
        refs = [nd.attrib["ref"] for nd in way.findall("nd")]
        retained_indices = []
        for index, node_id in enumerate(refs):
            if way_distance(way_id, coordinates(node_id)) <= MAX_ARM_DISTANCE_M:
                retained_indices.append(index)
        if not retained_indices:
            raise SystemExit(f"clipping retained no nodes in OSM way {way_id}")
        if retained_indices != list(range(retained_indices[0], retained_indices[-1] + 1)):
            raise SystemExit(f"clipping made OSM way {way_id} discontinuous")
        first, last = retained_indices[0], retained_indices[-1]
        kept = refs[first : last + 1]
        if first > 0:
            node_id = f"-9{way_id}1"
            synthetic_nodes[node_id] = boundary_point(
                way_id, refs[first - 1], refs[first]
            )
            kept.insert(0, node_id)
        if last < len(refs) - 1:
            node_id = f"-9{way_id}2"
            synthetic_nodes[node_id] = boundary_point(
                way_id, refs[last + 1], refs[last]
            )
            kept.append(node_id)
        if len(kept) < 2:
            raise SystemExit(f"clipping left fewer than two nodes in OSM way {way_id}")
        clipped_refs[way_id] = kept

    required_nodes = {node_id for refs in clipped_refs.values() for node_id in refs}
    signal_nodes = {node_id for junction in JUNCTIONS for node_id in junction["osm_ids"]}
    missing_signal_nodes = signal_nodes.difference(required_nodes)
    if missing_signal_nodes:
        raise SystemExit(f"subset does not contain signal nodes: {sorted(missing_signal_nodes)}")

    subset = ET.Element(
        "osm", {"version": "0.6", "generator": "build_gyeongju_industrial.py"}
    )
    for node_id in sorted(required_nodes, key=int):
        if node_id in synthetic_nodes:
            lat, lon = synthetic_nodes[node_id]
            node_attrib = {"id": node_id, "lat": f"{lat:.10f}", "lon": f"{lon:.10f}"}
            tags = {}
        else:
            node = source_nodes[node_id]
            node_attrib = {key: node.attrib[key] for key in ("id", "lat", "lon")}
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in node.findall("tag")}
        out = ET.SubElement(
            subset,
            "node",
            node_attrib,
        )
        if node_id in signal_nodes:
            tags["highway"] = "traffic_signals"
        for key in sorted(tags):
            ET.SubElement(out, "tag", {"k": key, "v": tags[key]})

    for way_id in sorted(WAY_ANCHORS, key=int):
        way = source_ways[way_id]
        out = ET.SubElement(subset, "way", {"id": way_id})
        for node_id in clipped_refs[way_id]:
            ET.SubElement(out, "nd", {"ref": node_id})
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if way_id in TWO_LANE_ONEWAY or way_id in TWO_LANE_TWOWAY:
            tags["lanes"] = "2"
        elif way_id in ONE_LANE_ONEWAY:
            tags["lanes"] = "1"
        if "maxspeed" not in tags:
            tags["maxspeed"] = "70" if way_id in MAIN_ROAD_WAYS else "50"
        for key in sorted(tags):
            ET.SubElement(out, "tag", {"k": key, "v": tags[key]})

    ET.indent(subset, space="  ")
    ET.ElementTree(subset).write(subset_path, encoding="utf-8", xml_declaration=True)


def locate_and_rename_tls(net_path: Path) -> dict[str, str]:
    root = ET.parse(net_path).getroot()
    source_ids = [tls.attrib["id"] for tls in root.findall("tlLogic")]
    # netconvert shortens large joined-junction IDs with ``#Nmore``.  Match
    # each TLS by a unique incoming OSM way as well as by its representative
    # node so the result does not depend on whether that node survives in the
    # displayed cluster ID.
    markers = {
        "gj_yonggang": ("529483975", "1435311645"),
        "gj_geunhwa": ("436736928", "1171256005"),
        "gj_seungsam": ("436736929", "1330059384"),
    }
    junctions = {junction.attrib["id"]: junction for junction in root.findall("junction")}
    renames = {}
    for canonical_id, (node_marker, edge_marker) in markers.items():
        matches = [
            source_id
            for source_id in source_ids
            if node_marker in source_id
            or edge_marker in junctions[source_id].attrib.get("incLanes", "")
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"expected one source TLS for {canonical_id}, got {matches} from {source_ids}"
            )
        renames[matches[0]] = canonical_id
    if len(source_ids) != 3:
        raise SystemExit(f"expected exactly three traffic lights, got {source_ids}")

    xml = net_path.read_text(encoding="utf-8")
    for source_id, canonical_id in sorted(renames.items(), key=lambda item: -len(item[0])):
        xml = xml.replace(source_id, canonical_id)
    net_path.write_text(xml, encoding="utf-8", newline="\n")
    return {canonical: source for source, canonical in renames.items()}


def normalize_tls_programs(net_path: Path) -> None:
    tree = ET.parse(net_path)
    root = tree.getroot()
    for tls in root.findall("tlLogic"):
        # Geunhwa drains its north approach while Seungsam serves its east
        # approach (t=35..65 modulo 140), preventing repeated exit blockage.
        tls.set("offset", "105" if tls.attrib["id"] == "gj_geunhwa" else "0")
        source_green_states = [
            phase.attrib["state"]
            for phase in tls.findall("phase")
            if "y" not in phase.attrib["state"]
            and any(signal in phase.attrib["state"] for signal in "Gg")
        ]
        junction = next(
            node for node in root.findall("junction") if node.attrib["id"] == tls.attrib["id"]
        )
        foes = {
            int(request.attrib["index"]): request.attrib["foes"][::-1]
            for request in junction.findall("request")
        }
        green_states = []
        incoming_lanes = {
            int(c.attrib["linkIndex"]): (c.attrib["from"], c.attrib["fromLane"])
            for c in root.findall("connection") if c.attrib.get("tl") == tls.attrib["id"]
        }
        for source_state in source_green_states:
            # Joined geometry may make links from the same approach foes.
            # Keep all turns from one lane together: separating them traps a
            # green-bound vehicle behind a red-bound vehicle on that lane.
            if "g" in source_state:
                raise SystemExit("incoming layout must produce protected greens")
            lane_groups: dict[tuple[str, str], list[int]] = {}
            for link_index, signal in enumerate(source_state):
                if signal == "G":
                    lane_groups.setdefault(incoming_lanes[link_index], []).append(link_index)
            groups: list[list[int]] = []
            for lane_links in lane_groups.values():
                if any(foes[a][b] == "1" or foes[b][a] == "1"
                       for a in lane_links for b in lane_links if a != b):
                    raise SystemExit(f"Conflicting turns within one lane at {tls.attrib['id']}: {lane_links}")
                group = next(
                    (
                        candidate
                        for candidate in groups
                        if all(
                            foes[link_index][other] == "0" and foes[other][link_index] == "0"
                            for link_index in lane_links for other in candidate
                        )
                    ),
                    None,
                )
                if group is None:
                    group = []
                    groups.append(group)
                group.extend(lane_links)
            for group in groups:
                state = ["r"] * len(source_state)
                for link_index in group:
                    state[link_index] = "G"
                green_states.append("".join(state))
        for phase in list(tls):
            if phase.tag == "phase":
                tls.remove(phase)
        for state in green_states:
            ET.SubElement(tls, "phase", {"duration": "30", "state": state})
            yellow = "".join("y" if signal in "Gg" else "r" for signal in state)
            ET.SubElement(tls, "phase", {"duration": "3", "state": yellow})
            ET.SubElement(tls, "phase", {"duration": "2", "state": "r" * len(state)})
    ET.indent(tree, space="    ")
    tree.write(net_path, encoding="utf-8", xml_declaration=True)


def run_netconvert(subset_path: Path, net_path: Path, netconvert: str) -> dict[str, str]:
    command = [
        netconvert,
        "--osm-files", str(subset_path),
        "--output-file", str(net_path),
        "--junctions.join", "true",
        "--junctions.join-dist", "35",
        "--junctions.join.parallel-threshold", "0",
        "--tls.green.time", "30",
        "--tls.yellow.time", "3",
        "--tls.allred.time", "2",
        "--tls.layout", "incoming",
        "--no-turnarounds", "true",
        "--no-turnarounds.tls", "true",
        "--osm.sidewalks", "false",
        "--osm.stop-output.length", "0",
        "--roundabouts.guess", "false",
        "--ramps.guess", "false",
        "--remove-edges.isolated", "true",
        "--junctions.corner-detail", "5",
        "--junctions.internal-link-detail", "8",
        "--default.junctions.radius", "12",
    ]
    subprocess.run(command, check=True)
    source_clusters = locate_and_rename_tls(net_path)
    normalize_tls_programs(net_path)
    return source_clusters


def read_tls_programs(net_path: Path) -> dict[str, dict[str, object]]:
    root = ET.parse(net_path).getroot()
    programs: dict[str, dict[str, object]] = {}
    for tls in root.findall("tlLogic"):
        phases = []
        for index, phase in enumerate(tls.findall("phase")):
            state = phase.attrib["state"]
            if any(signal in state for signal in "Gg"):
                kind = "green"
            elif "y" in state:
                kind = "yellow"
            else:
                kind = "all_red"
            phases.append(
                {
                    "index": index,
                    "duration": int(float(phase.attrib["duration"])),
                    "state": state,
                    "kind": kind,
                }
            )
        programs[tls.attrib["id"]] = {
            "program_id": tls.attrib.get("programID", "0"),
            "offset_s": float(tls.attrib.get("offset", "0")),
            "phases": phases,
            "controlled_movements": [],
        }
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl")
        if tls_id not in programs:
            continue
        programs[tls_id]["controlled_movements"].append(
            {
                "link_index": int(connection.attrib["linkIndex"]),
                "from_edge": connection.attrib["from"],
                "from_lane": int(connection.attrib["fromLane"]),
                "to_edge": connection.attrib["to"],
                "to_lane": int(connection.attrib["toLane"]),
                "direction": connection.attrib["dir"],
            }
        )
    for program in programs.values():
        program["controlled_movements"].sort(
            key=lambda movement: (movement["link_index"], movement["from_lane"])
        )
    return programs


def write_contract(
    output_dir: Path,
    net_path: Path,
    subset_path: Path,
    source_clusters: dict[str, str],
) -> None:
    # Keep the contract byte-identical when the same bundle is regenerated in
    # a different output directory.
    network_ref = net_path.relative_to(output_dir).as_posix()
    subset_ref = subset_path.relative_to(output_dir).as_posix()
    contract = {
        "schema_version": 1,
        "network": network_ref,
        "corridor_order": ["gj_yonggang", "gj_geunhwa", "gj_seungsam"],
        "junctions": JUNCTIONS,
        "external_boundaries": [
            {
                "id": "yonggang_southwest",
                "junction": "gj_yonggang",
                "direction_from_junction": "southwest",
                "fringe_nodes": {
                    "inbound": "-911650143121",
                    "outbound": "-911650143112",
                },
                "edges": {"inbound": "1165014312#0", "outbound": "1165014311"},
            },
            {
                "id": "yonggang_southeast",
                "junction": "gj_yonggang",
                "direction_from_junction": "southeast",
                "fringe_nodes": {
                    "inbound": "-914353079931",
                    "outbound": "-914366779892",
                },
                "edges": {"inbound": "1435307993", "outbound": "1436677989#1"},
            },
            {
                "id": "yonggang_west",
                "junction": "gj_yonggang",
                "direction_from_junction": "west",
                "fringe_nodes": {
                    "inbound": "-914316832031",
                    "outbound": "-914316832031",
                },
                "edges": {"inbound": "1431683203", "outbound": "-1431683203"},
            },
            {
                "id": "geunhwa_west",
                "junction": "gj_geunhwa",
                "direction_from_junction": "west",
                "fringe_nodes": {"inbound": "-9424106051", "outbound": "-9424106051"},
                "edges": {"inbound": "42410605", "outbound": "-42410605"},
            },
            {
                "id": "geunhwa_east",
                "junction": "gj_geunhwa",
                "direction_from_junction": "east",
                "fringe_nodes": {
                    "inbound": "-915104389902",
                    "outbound": "-915104389902",
                },
                "edges": {"inbound": "-1510438990", "outbound": "1510438990"},
            },
            {
                "id": "seungsam_west",
                "junction": "gj_seungsam",
                "direction_from_junction": "west",
                "fringe_nodes": {"inbound": "529483978", "outbound": "529483978"},
                "edges": {"inbound": "1330059384", "outbound": "-1330059384"},
            },
            {
                "id": "seungsam_east",
                "junction": "gj_seungsam",
                "direction_from_junction": "east",
                "fringe_nodes": {
                    "inbound": "-95501375981",
                    "outbound": "-95501375981",
                },
                "edges": {"inbound": "550137598", "outbound": "-550137598"},
            },
            {
                "id": "seungsam_north",
                "junction": "gj_seungsam",
                "direction_from_junction": "north",
                "fringe_nodes": {
                    "inbound": "-914316853421",
                    "outbound": "-915104389922",
                },
                "edges": {"inbound": "1431685342#0", "outbound": "1510438992#1"},
            },
        ],
        "tls_programs": read_tls_programs(net_path),
        "source_osm": {
            "url": SOURCE_URL,
            "retrieved_date": SOURCE_DATE,
            "sha256": SOURCE_SHA256,
            "license": "OpenStreetMap contributors, ODbL 1.0",
            "subset": subset_ref,
            "subset_sha256": sha256(subset_path),
            "way_ids": sorted(WAY_ANCHORS, key=int),
            "junction_clusters": source_clusters,
        },
        "lane_assumptions": {
            "osm_explicit_lanes_2": {
                "meaning": "OSM supplied lanes=2; no lane-count override was needed.",
                "way_ids": sorted(OSM_EXPLICIT_LANES_2, key=int),
            },
            "divided_main_carriageways": {
                "lanes_per_direction": 2,
                "basis": "Adjacent OSM carriageways are tagged lanes=2; missing parallel carriageway tags are model overrides, not field observations.",
                "way_ids": sorted(MISSING_MAIN_LANE_OVERRIDES, key=int),
            },
            "local_two_way_arms": {
                "lanes_total": 2,
                "basis": "Missing lane counts are modeled as one lane per direction.",
                "way_ids": sorted(MISSING_LOCAL_LANE_OVERRIDES, key=int),
            },
            "one_way_slip_links": {
                "lanes": 1,
                "basis": "Missing lane counts are modeled as one lane.",
                "way_ids": sorted(ONE_LANE_ONEWAY, key=int),
            },
        },
        "speed_assumptions": {
            "osm_explicit_70_kmh": sorted(OSM_EXPLICIT_MAXSPEED_70, key=int),
            "main_road_missing_tag_70_kmh": sorted(
                MAIN_ROAD_WAYS.difference(OSM_EXPLICIT_MAXSPEED_70), key=int
            ),
            "local_or_slip_missing_tag_50_kmh": sorted(
                set(WAY_ANCHORS).difference(MAIN_ROAD_WAYS).difference(
                    OSM_EXPLICIT_MAXSPEED_70
                ),
                key=int,
            ),
        },
        "assumptions": [
            "The three traffic lights and their 30 s green, 3 s yellow, 2 s all-red programs are simulation definitions; field signal phases and timings were not observed.",
            "Main-road ways missing maxspeed use 70 km/h based on adjacent tagged OSM segments; local arms and slip links missing maxspeed use 50 km/h. These are model inputs, not new field measurements.",
            "Official 2024 and 2026 photographs show a three-to-four-lane expansion near Yonggang and Cheonggangsa, but the photographed segment and direction cannot be mapped reliably to a specific OSM way from the published material. No way-specific lane override is attributed to that project.",
            "Recent road-expansion lane geometry cannot be reproduced completely from OSM and may differ from official photographs; this is a map-based simulation model, not a current surveyed road inventory.",
            "Intermediate alleys are omitted with zero inflow and outflow. Pedestrians, parking, and U-turns are excluded.",
            "Passenger cars, trucks, and buses are permitted by the road network; demand composition is defined outside this builder.",
            "A default junction radius of 12 m is used to keep Seungsam turns usable by the simulated vehicle classes; it also applies to the other signals and some merging junctions. This is a model assumption, not a survey measurement.",
            "The incoming TLS layout gives each incoming road a separate protected green phase. Netconvert regenerates the right-of-way rules and internal paths. Protected foes within an approach are separated by incoming lane, keeping all turns from each lane together to prevent head-of-line blocking. Every group uses 30 s green, 3 s yellow and 2 s all-red. This conservative experimental plan is not a measured or optimized field signal plan.",
            "Geunhwa has a modeled SUMO offset of 105 s; its north-approach green overlaps Seungsam's east-approach green at t=35..65 modulo 140 s. This avoids the repeated downstream blockage observed with zero offsets. Other offsets are zero. Field coordination was not measured.",
        ],
    }
    (output_dir / "contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        help="Pinned full OSM response; verifies SHA-256 and refreshes the subset.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--netconvert", default="netconvert")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    prepare_output(output_dir, args.overwrite)
    subset_path = output_dir / "source" / "industrial_corridor.osm.xml"
    net_path = output_dir / "industrial.net.xml"
    if args.source:
        write_subset(args.source.resolve(), subset_path)
    elif output_dir == DEFAULT_OUTPUT_DIR.resolve() and subset_path.exists():
        pass
    elif DEFAULT_OUTPUT_DIR.joinpath("source", "industrial_corridor.osm.xml").exists():
        shutil.copy2(
            DEFAULT_OUTPUT_DIR / "source" / "industrial_corridor.osm.xml", subset_path
        )
    else:
        raise SystemExit("bundled subset missing; provide --source")

    source_clusters = run_netconvert(subset_path, net_path, args.netconvert)
    write_contract(output_dir, net_path, subset_path, source_clusters)


if __name__ == "__main__":
    main()
