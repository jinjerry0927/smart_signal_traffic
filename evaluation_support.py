"""동결 수요와 매초 측정: 평가 결과의 단위·집계·누락 차량을 명시한다."""
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze_departures(source, destination, seconds):
    """프로젝트의 결정론적 flow를 동일 ID·출발 시각의 vehicle 목록으로 동결한다.

    지원하지 않는 확률·경로 분포는 추정하지 않는다. 종료 시각의 출발은 제외한다.
    """
    original = ET.parse(source).getroot()
    result = ET.Element("routes")
    vehicles = []
    for item in original:
        if item.tag in ("vType", "route"):
            result.append(deepcopy(item))
        elif item.tag == "flow":
            supported = {"id", "type", "route", "begin", "end", "vehsPerHour",
                         "departLane", "departSpeed", "departPos"}
            if set(item.attrib) - supported or "vehsPerHour" not in item.attrib:
                raise ValueError("평가 flow는 결정론적 vehsPerHour 형식이어야 합니다.")
            rate = Decimal(item.attrib["vehsPerHour"])
            if rate <= 0:
                raise ValueError("vehsPerHour는 양수여야 합니다.")
            begin = Decimal(item.get("begin", "0"))
            end = min(Decimal(item.get("end", str(seconds))), Decimal(seconds))
            index = 0
            while (depart := begin + Decimal(index) * 3600 / rate) < end:
                attrs = {key: value for key, value in item.attrib.items()
                         if key not in {"id", "begin", "end", "vehsPerHour"}}
                attrs.update(id=f"{item.attrib['id']}.{index}", depart=format(depart, ".9f"))
                vehicles.append((depart, attrs["id"], ET.Element("vehicle", attrs)))
                index += 1
        elif item.tag == "vehicle":
            depart = Decimal(item.attrib["depart"])
            if depart < seconds:
                vehicles.append((depart, item.attrib["id"], deepcopy(item)))
        else:
            raise ValueError(f"지원하지 않는 수요 요소: {item.tag}")
    for _, _, vehicle in sorted(vehicles, key=lambda row: (row[0], row[1])):
        result.append(vehicle)
    ET.ElementTree(result).write(destination, encoding="utf-8", xml_declaration=True)
    return len(vehicles)


class EpisodeMetrics:
    """SUMO 매 1초 후 호출한다. 현재 대기의 합과 완료 차량 통계를 구분한다."""

    def __init__(self):
        self.waits, self.queues = [], []
        self.departed, self.arrived = set(), set()
        self.teleports = 0
        self.collisions = 0

    def sample(self, connection):
        sim = connection.simulation
        self.departed.update(sim.getDepartedIDList())
        self.arrived.update(sim.getArrivedIDList())
        self.teleports += sim.getStartingTeleportNumber()
        self.collisions += sim.getCollidingVehiclesNumber()
        ids = connection.vehicle.getIDList()
        self.waits.append(sum(connection.vehicle.getWaitingTime(v) for v in ids))
        self.queues.append(sum(connection.vehicle.getSpeed(v) < 0.1 for v in ids))

    def finish(self, tripinfo, running, pending, scheduled):
        trips = [t for t in ET.parse(tripinfo).getroot().findall("tripinfo")
                 if float(t.attrib["arrival"]) >= 0]
        if len(trips) != len(self.arrived):
            raise AssertionError("매초 도착 수와 tripinfo 완료 차량 수가 다릅니다.")
        mean = lambda values: sum(values) / len(values) if values else None
        return {
            "mean_system_total_waiting_time_s": mean(self.waits),
            "mean_queue_vehicles": mean(self.queues),
            "max_queue_vehicles": max(self.queues, default=0),
            "arrived_vehicles": len(self.arrived),
            "departed_vehicles": len(self.departed),
            "scheduled_vehicles": scheduled,
            "not_departed_vehicles": scheduled - len(self.departed),
            "departed_not_arrived_vehicles": len(self.departed - self.arrived),
            "running_vehicles_end": running,
            "pending_vehicles_end": pending,
            "teleport_events": self.teleports,
            "collision_vehicle_events": self.collisions,
            "mean_completed_travel_time_s": mean([float(t.attrib["duration"]) for t in trips]),
            "mean_completed_waiting_time_s": mean([float(t.attrib["waitingTime"]) for t in trips]),
            "mean_completed_time_loss_s": mean([float(t.attrib["timeLoss"]) for t in trips]),
            "sample_seconds": len(self.waits),
        }
