from __future__ import annotations


class HeuristicAgent:
    def act(self, observation: dict) -> dict:
        actions = []
        runway_occupied = observation["snapshot"]["airport"]["runway_occupied_by"] is not None
        for dp in observation["decision_points"]:
            aircraft = dp["aircraft"][0]
            if dp["type"] in {"predicted_conflict", "active_conflict"}:
                actions.append({"aircraft": aircraft, "type": "assign_heading", "heading": 45})
            elif dp["type"] == "runway_occupied_on_final":
                actions.append({"aircraft": aircraft, "type": "go_around"})
            elif dp["type"] == "departure_ready" and not runway_occupied:
                actions.append({"aircraft": aircraft, "type": "clear_for_takeoff"})
            elif dp["type"] == "emergency" and not runway_occupied:
                actions.append({"aircraft": aircraft, "type": "clear_to_land"})
        if not actions:
            actions.append({"aircraft": next(iter(observation["snapshot"]["aircraft"])), "type": "no_op"})
        return {"actions": actions}
