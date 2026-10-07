"""Build the three-signal Gyeongju corridor from a pinned OSM subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_DIR = ROOT / "network" / "gyeongju_corridor"
SUBSET_PATH = NETWORK_DIR / "source" / "gyeongju_corridor.osm.xml"
NET_PATH = NETWORK_DIR / "corridor.net.xml"
CONTRACT_PATH = NETWORK_DIR / "contract.json"

SOURCE_URL = (
    "https://api.openstreetmap.org/api/0.6/map?"
    "bbox=129.199,35.849,129.213,35.861"
)
SOURCE_DATE = "2026-09-17"
SOURCE_SHA256 = "91fb1a319ebaedae234fa5b4ff3d1ee904e5011ab0e44f38775df0d5e55c559b"

# Only the three junctions' principal roads are retained. Small side streets
# between the signals are deliberately omitted and have zero modelled demand.
WAY_IDS = {
    "42436484",    # Geumseong-ro 396beon-gil, south-east arm
    "912222341",   # Geumseong-ro 395beon-gil, south-west arm
    "983627969",   # Geumseong-ro northbound, south of gj_south
    "1164614095",  # Geumseong-ro northbound, gj_south to gj_center
    "642113878",   # Geumseong-ro northbound, gj_center to gj_north
    "1167498389",  # Geumseong-ro southbound, gj_center through gj_south
    "1334626930",  # Geumseong-ro southbound, gj_north to gj_center
    "361265530",   # Dongdae-ro, center-west arm
    "1334626931",  # short center junction connector
    "533136972",   # public-housing road, center-east arm
    "1164614093",  # Alcheonnam-ro, north-west arm and junction connector
    "1164614094",  # Alcheonnam-ro, north-east arm
}

# Geographic crop. Fringe nodes fall about 220-280 m from the nearest signal,
# depending on the spacing of OSM shape nodes.
BOUNDARY = (129.2038, 35.8507, 129.2105, 35.8595)
MAX_ARM_DISTANCE_M = 300.0

JUNCTIONS = [
    {
        "id": "gj_south",
        "label": "금성로 남측 교차로",
        "lat": 35.85305,
        "lon": 129.20683,
        "osm_ids": ["10857621941", "699850140"],
    },
    {
        "id": "gj_center",
        "label": "동대사거리",
        "lat": 35.85525,
        "lon": 129.20679,
        "osm_ids": ["10857621938", "414664600"],
    },
    {
        "id": "gj_north",
        "label": "금성로-알천남로 교차로",
        "lat": 35.85723,
        "lon": 129.20764,
        "osm_ids": ["529458586", "5373391858"],
    },
]

ID_RENAMES = {
    "cluster_10857621941_699850140": "gj_south",
    "cluster_10857621938_414664600": "gj_center",
    "cluster_529458586_5373391858": "gj_north",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_subset(source: Path) -> None:
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
        if way.attrib["id"] in WAY_IDS
    }
    missing = WAY_IDS.difference(source_ways)
    if missing:
        raise SystemExit(f"source is missing required OSM ways: {sorted(missing)}")

    anchors = [(junction["lat"], junction["lon"]) for junction in JUNCTIONS]

    def distance_to_corridor(node_id: str) -> float:
        node = source_nodes[node_id]
        lat = float(node.attrib["lat"])
        lon = float(node.attrib["lon"])
        return min(
            math.hypot((lat - anchor_lat) * 111_111, (lon - anchor_lon) * 90_000)
            for anchor_lat, anchor_lon in anchors
        )

    clipped_refs = {
        way_id: [
            nd.attrib["ref"]
            for nd in way.findall("nd")
            if distance_to_corridor(nd.attrib["ref"]) <= MAX_ARM_DISTANCE_M
        ]
        for way_id, way in source_ways.items()
    }
    too_short = [way_id for way_id, refs in clipped_refs.items() if len(refs) < 2]
    if too_short:
        raise SystemExit(f"clipping left fewer than two nodes in ways: {too_short}")
    required_nodes = {node_id for refs in clipped_refs.values() for node_id in refs}
    subset = ET.Element(
        "osm", {"version": "0.6", "generator": "build_gyeongju_corridor.py"}
    )
    ET.SubElement(
        subset,
        "bounds",
        {
            "minlon": str(BOUNDARY[0]),
            "minlat": str(BOUNDARY[1]),
            "maxlon": str(BOUNDARY[2]),
            "maxlat": str(BOUNDARY[3]),
        },
    )
    for node_id in sorted(required_nodes, key=int):
        node = source_nodes[node_id]
        out = ET.SubElement(
            subset,
            "node",
            {key: node.attrib[key] for key in ("id", "lat", "lon")},
        )
        for tag in node.findall("tag"):
            ET.SubElement(out, "tag", dict(tag.attrib))

    for way_id in sorted(WAY_IDS, key=int):
        way = source_ways[way_id]
        out = ET.SubElement(subset, "way", {"id": way_id})
        for node_id in clipped_refs[way_id]:
            ET.SubElement(out, "nd", {"ref": node_id})
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if "maxspeed" not in tags:
            tags["maxspeed"] = "50"
        for key in sorted(tags):
            ET.SubElement(out, "tag", {"k": key, "v": tags[key]})

    ET.indent(subset, space="  ")
    SUBSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(subset).write(SUBSET_PATH, encoding="utf-8", xml_declaration=True)


def run_netconvert(netconvert: str) -> None:
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    boundary = ",".join(str(value) for value in BOUNDARY)
    command = [
        netconvert,
        "--osm-files", str(SUBSET_PATH),
        "--output-file", str(NET_PATH),
        "--keep-edges.in-geo-boundary", boundary,
        "--keep-edges.by-vclass", "passenger",
        "--junctions.join", "true",
        "--junctions.join-dist", "20",
        "--tls.green.time", "30",
        "--tls.yellow.time", "3",
        "--tls.allred.time", "2",
        "--no-turnarounds", "true",
        "--no-turnarounds.tls", "true",
        "--osm.sidewalks", "false",
        "--roundabouts.guess", "false",
        "--ramps.guess", "false",
        "--remove-edges.isolated", "true",
        "--junctions.corner-detail", "5",
    ]
    subprocess.run(command, check=True)
    xml = NET_PATH.read_text(encoding="utf-8")
    for source_id, canonical_id in ID_RENAMES.items():
        xml = xml.replace(source_id, canonical_id)
    NET_PATH.write_text(xml, encoding="utf-8", newline="\n")
    normalize_tls_programs()


def normalize_tls_programs() -> None:
    """Apply a 30 s green, 3 s yellow, 2 s all-red cycle to each green group."""
    tree = ET.parse(NET_PATH)
    root = tree.getroot()
    for tls in root.findall("tlLogic"):
        green_states = [
            phase.attrib["state"]
            for phase in tls.findall("phase")
            if "y" not in phase.attrib["state"]
            and any(signal in phase.attrib["state"] for signal in "Gg")
        ]
        for phase in list(tls):
            if phase.tag == "phase":
                tls.remove(phase)
        for state in green_states:
            ET.SubElement(tls, "phase", {"duration": "30", "state": state})
            yellow = "".join("y" if signal in "Gg" else "r" for signal in state)
            ET.SubElement(tls, "phase", {"duration": "3", "state": yellow})
            ET.SubElement(tls, "phase", {"duration": "2", "state": "r" * len(state)})
    ET.indent(tree, space="    ")
    tree.write(NET_PATH, encoding="utf-8", xml_declaration=True)


def tls_programs() -> dict[str, dict[str, object]]:
    root = ET.parse(NET_PATH).getroot()
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
            "phases": phases,
            "controlled_movements": [],
        }
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl")
        if tls_id not in programs:
            continue
        movement = {
            "link_index": int(connection.attrib["linkIndex"]),
            "from_edge": connection.attrib["from"],
            "from_lane": int(connection.attrib["fromLane"]),
            "to_edge": connection.attrib["to"],
            "to_lane": int(connection.attrib["toLane"]),
            "direction": connection.attrib["dir"],
        }
        programs[tls_id]["controlled_movements"].append(movement)
    for program in programs.values():
        program["controlled_movements"].sort(
            key=lambda movement: (movement["link_index"], movement["from_lane"])
        )
    return programs


def write_contract() -> None:
    programs = tls_programs()
    expected = {junction["id"] for junction in JUNCTIONS}
    if set(programs) != expected:
        raise SystemExit(
            "unexpected traffic-light IDs: "
            f"expected {sorted(expected)}, got {sorted(programs)}"
        )

    contract = {
        "schema_version": 1,
        "network": "network/gyeongju_corridor/corridor.net.xml",
        "junctions": JUNCTIONS,
        "external_boundaries": [
            {
                "id": "south_south",
                "junction": "gj_south",
                "direction_from_junction": "south",
                "fringe_nodes": {
                    "inbound": "529807761",
                    "outbound": "10857621947",
                },
                "edges": {
                    "inbound": "983627969#0",
                    "outbound": "1167498389#1",
                },
            },
            {
                "id": "south_west",
                "junction": "gj_south",
                "direction_from_junction": "west",
                "fringe_nodes": {"inbound": "699849783", "outbound": "699849783"},
                "edges": {
                    "inbound": "-912222341#1",
                    "outbound": "912222341#1",
                },
            },
            {
                "id": "south_east",
                "junction": "gj_south",
                "direction_from_junction": "east",
                "fringe_nodes": {"inbound": "2122131950", "outbound": "2122131950"},
                "edges": {"inbound": "42436484", "outbound": "-42436484"},
            },
            {
                "id": "center_west",
                "junction": "gj_center",
                "direction_from_junction": "west",
                "fringe_nodes": {"inbound": "530538242", "outbound": "530538242"},
                "edges": {"inbound": "361265530", "outbound": "-361265530"},
            },
            {
                "id": "center_east",
                "junction": "gj_center",
                "direction_from_junction": "east",
                "fringe_nodes": {"inbound": "530538318", "outbound": "530538318"},
                "edges": {"inbound": "-533136972", "outbound": "533136972"},
            },
            {
                "id": "north_west",
                "junction": "gj_north",
                "direction_from_junction": "west",
                "fringe_nodes": {"inbound": "530538351", "outbound": "530538351"},
                "edges": {
                    "inbound": "1164614093#0",
                    "outbound": "-1164614093#0",
                },
            },
            {
                "id": "north_east",
                "junction": "gj_north",
                "direction_from_junction": "east",
                "fringe_nodes": {"inbound": "529807863", "outbound": "529807863"},
                "edges": {"inbound": "-1164614094", "outbound": "1164614094"},
            },
        ],
        "tls_programs": programs,
        "source_osm": {
            "url": SOURCE_URL,
            "retrieved_date": SOURCE_DATE,
            "sha256": SOURCE_SHA256,
            "license": "OpenStreetMap contributors, ODbL 1.0",
            "subset": "network/gyeongju_corridor/source/gyeongju_corridor.osm.xml",
            "way_ids": sorted(WAY_IDS, key=int),
            "junction_clusters": {
                canonical_id: source_id
                for source_id, canonical_id in ID_RENAMES.items()
            },
        },
        "assumptions": [
            "All modeled roads use 50 km/h where OSM has no maxspeed tag; this is a simulation assumption, not a surveyed value.",
            "OSM way 642113878 has no lanes tag, so SUMO's one-lane one-way default is retained.",
            "The two-way, four-lane Alcheonnam-ro OSM ways are modeled as two lanes per direction.",
            "Unspecified turn-lane data is not treated as field-observed lane allocation.",
            "Intermediate living/service/residential streets, pedestrian facilities, bus stops, parking, bicycles, and U-turns are omitted; their inflow and outflow are zero.",
            "Signal green groups come from SUMO's imported G/g link priorities; each green stage is normalized to 30 s followed by 3 s yellow and 2 s all-red.",
        ],
    }
    CONTRACT_PATH.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        help="Pinned full OSM response; verifies SHA-256 and refreshes the subset.",
    )
    parser.add_argument("--netconvert", default="netconvert")
    args = parser.parse_args()

    if args.source:
        write_subset(args.source.resolve())
    elif not SUBSET_PATH.exists():
        raise SystemExit("bundled subset missing; provide --source")
    run_netconvert(args.netconvert)
    write_contract()


if __name__ == "__main__":
    main()
