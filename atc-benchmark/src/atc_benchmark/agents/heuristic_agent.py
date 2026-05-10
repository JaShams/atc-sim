from __future__ import annotations


class HeuristicAgent:
    _PRIORITY = {
        "emergency": 0,
        "active_conflict": 1,
        "predicted_conflict": 2,
        "runway_occupied_on_final": 3,
        "departure_ready": 4,
    }

    def _priority_key(self, decision_point: dict) -> tuple[int, str, str]:
        dp_type = decision_point.get("type", "")
        aircraft = decision_point.get("aircraft") or [""]
        callsign = aircraft[0]
        return (self._PRIORITY.get(dp_type, 99), callsign, dp_type)

    def _action_for(self, dp: dict, runway_occupied: bool) -> dict | None:
        aircraft = dp["aircraft"][0]
        dp_type = dp.get("type")
        if dp_type == "emergency" and not runway_occupied:
            return {"aircraft": aircraft, "type": "clear_to_land"}
        if dp_type == "active_conflict":
            return {"aircraft": aircraft, "type": "assign_heading", "heading": 45}
        if dp_type == "predicted_conflict":
            return {"aircraft": aircraft, "type": "assign_heading", "heading": 60}
        if dp_type == "runway_occupied_on_final":
            return {"aircraft": aircraft, "type": "go_around"}
        if dp_type == "departure_ready" and not runway_occupied:
            return {"aircraft": aircraft, "type": "clear_for_takeoff"}
        return None

    def act(self, observation: dict) -> dict:
        actions = []
        assigned: set[str] = set()
        runway_occupied = observation["snapshot"]["airport"]["runway_occupied_by"] is not None

        for dp in sorted(observation["decision_points"], key=self._priority_key):
            aircraft = dp.get("aircraft", [])
            if not aircraft:
                continue
            callsign = aircraft[0]
            if callsign in assigned:
                continue
            action = self._action_for(dp, runway_occupied)
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
