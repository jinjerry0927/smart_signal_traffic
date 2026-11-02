"""Read-only TraCI subscriptions for the browser; never advances or controls SUMO."""
import traci.constants as tc

VARIABLES = (tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_LENGTH,
             tc.VAR_WIDTH, tc.VAR_VEHICLECLASS, tc.VAR_LANE_ID, tc.VAR_LANEPOSITION)


class VehicleFrames:
    def __init__(self):
        self.subscribed = set()

    def snapshot(self, connection):
        vehicles = connection.vehicle
        ids = list(vehicles.getIDList())
        live = set(ids)
        for vid in live - self.subscribed:
            # Subscription values are returned immediately and refreshed by SUMO.
            vehicles.subscribe(vid, VARIABLES)
        self.subscribed = live
        values = vehicles.getAllSubscriptionResults()
        result = []
        for vid in ids:
            row = values[vid]
            x, y = row[tc.VAR_POSITION]
            result.append(dict(id=vid, x=x, y=y, angle=row[tc.VAR_ANGLE], speed=row[tc.VAR_SPEED],
                               length=row[tc.VAR_LENGTH], width=row[tc.VAR_WIDTH],
                               vehicle_class=row[tc.VAR_VEHICLECLASS], lane_id=row[tc.VAR_LANE_ID],
                               lane_position=row[tc.VAR_LANEPOSITION]))
        return result
