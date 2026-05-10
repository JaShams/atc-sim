from __future__ import annotations

from math import atan2, cos, degrees, radians, sin


class HeuristicAgent:
    _PRIORITY = {
        "emergency": 0,
        "wind_runway_mismatch": 1,
        "active_conflict": 2,
        "predicted_conflict": 3,
        "runway_occupied_on_final": 4,
        "departure_ready": 5,
    }

    def _priority_key(self, decision_point: dict) -> tuple[int, str, str]:
        dp_type = decision_point.get("type", "")
        aircraft = decision_point.get("aircraft") or [""]
        callsign = aircraft[0]
        return (self._PRIORITY.get(dp_type, 99), callsign, dp_type)

    def _runway_heading(self, snapshot: dict) -> float:
        runway = snapshot["airport"].get("active_runway") or snapshot["airport"].get("runway_id") or "36"
        n = int(runway)
        return 360.0 if n == 36 else float(n * 10)

    def _angle_delta(self, a: float, b: float) -> float:
        return abs((a - b + 180) % 360 - 180)

    def _is_clearable_arrival(self, callsign: str, snapshot: dict) -> bool:
        ac = snapshot["aircraft"].get(callsign, {})
        if not ac.get("role") and not ac.get("status"):
            return True
        if ac.get("role") != "arrival" or ac.get("status") not in {"airborne", "on_final"}:
            return False
        target_runway = ac.get("target_runway")
        active_runway = snapshot["airport"].get("active_runway")
        if target_runway is not None and target_runway != active_runway:
            return False
        runway_heading = self._runway_heading(snapshot)
        for final_heading in (runway_heading, (runway_heading + 180) % 360):
            rw_rad = radians(final_heading)
            ux, uy = sin(rw_rad), cos(rw_rad)
            along_final_nm = -(ac.get("x_nm", 0) * ux + ac.get("y_nm", 0) * uy)
            cross_track_nm = abs(ac.get("x_nm", 0) * uy - ac.get("y_nm", 0) * ux)
            if self._angle_delta(ac.get("heading_deg", 0), final_heading) <= 35 and 0 <= along_final_nm <= 15 and cross_track_nm <= 3:
                return True
        return False

    def _conflict_heading(self, callsign: str, dp: dict, snapshot: dict) -> int:
        aircraft = dp.get("aircraft", [])
        ac = snapshot["aircraft"].get(callsign)
        other = next((snapshot["aircraft"].get(cs) for cs in aircraft if cs != callsign), None)
        if not ac or not other:
            return int((self._runway_heading(snapshot) + 45) % 360)
        dx = ac["x_nm"] - other["x_nm"]
        dy = ac["y_nm"] - other["y_nm"]
        away = (degrees(atan2(dx, dy)) + 360) % 360
        return int(round(away / 5) * 5) % 360

    def _action_for(self, dp: dict, runway_occupied: bool, wind_mismatch: bool, snapshot: dict) -> dict | None:
        aircraft = dp["aircraft"][0]
        dp_type = dp.get("type")
        if dp_type == "wind_runway_mismatch":
            return None
        if dp_type == "emergency" and not runway_occupied and not wind_mismatch and self._is_clearable_arrival(aircraft, snapshot):
            return {"aircraft": aircraft, "type": "clear_to_land"}
        if dp_type == "emergency":
            return {"aircraft": aircraft, "type": "assign_heading", "heading": int(self._runway_heading(snapshot))}
        if dp_type == "active_conflict":
            return {"aircraft": aircraft, "type": "assign_heading", "heading": self._conflict_heading(aircraft, dp, snapshot)}
        if dp_type == "predicted_conflict":
            return {"aircraft": aircraft, "type": "assign_heading", "heading": self._conflict_heading(aircraft, dp, snapshot)}
        if dp_type == "runway_occupied_on_final":
            return {"aircraft": aircraft, "type": "go_around"}
        if dp_type == "departure_ready" and not runway_occupied and not wind_mismatch:
            return {"aircraft": aircraft, "type": "clear_for_takeoff"}
        return None

    def act(self, observation: dict) -> dict:
        actions = []
        assigned: set[str] = set()
        snapshot = observation["snapshot"]
        runway_occupied = snapshot["airport"]["runway_occupied_by"] is not None
        wind_mismatch = any(dp.get("type") == "wind_runway_mismatch" for dp in observation["decision_points"])

        for dp in sorted(observation["decision_points"], key=self._priority_key):
            aircraft = dp.get("aircraft", [])
            if not aircraft:
                continue
            callsign = aircraft[0]
            if callsign in assigned:
                continue
            action = self._action_for(dp, runway_occupied, wind_mismatch, snapshot)
            if action is None:
                continue
            actions.append(action)
            assigned.add(callsign)
            if action["type"] in {"clear_to_land", "clear_for_takeoff"}:
                runway_occupied = True

        if not actions and observation["snapshot"]["aircraft"]:
            fallback_cs = min(observation["snapshot"]["aircraft"].keys())
            actions.append({"aircraft": fallback_cs, "type": "no_op"})
        return {"actions": actions}
