from __future__ import annotations

import json
import select
import sys
from typing import Any


class HumanAgent:
    """Interactive human-in-the-loop controller.

    Reads one action per invocation from stdin. Returns a single-item actions list,
    or an empty list if user chooses/forces no-op.
    """

    def __init__(self, timeout_sec: float = 30.0, max_retries: int = 3):
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries

    def _summarize(self, observation: dict[str, Any]) -> None:
        snapshot = observation.get("snapshot", {}) if isinstance(observation, dict) else {}
        aircraft = snapshot.get("aircraft", {}) if isinstance(snapshot, dict) else {}
        airport = snapshot.get("airport", {}) if isinstance(snapshot, dict) else {}
        decision_points = observation.get("decision_points", []) if isinstance(observation, dict) else []

        emergency = [cs for cs, ac in aircraft.items() if isinstance(ac, dict) and ac.get("emergency")]
        active_conflicts = [dp for dp in decision_points if isinstance(dp, dict) and dp.get("type") == "active_conflict"]
        predicted_conflicts = [dp for dp in decision_points if isinstance(dp, dict) and dp.get("type") == "predicted_conflict"]

        print("\n--- Human agent tick ---")
        print(f"time_sec={observation.get('time_sec')}")
        print(f"aircraft_count={len(aircraft)} callsigns={sorted(aircraft.keys())}")
        print(
            "runway="
            f"{airport.get('active_runway')} occupied_by={airport.get('runway_occupied_by')} phase={airport.get('runway_phase')}"
        )
        print(
            "conflicts="
            f"active:{len(active_conflicts)} predicted:{len(predicted_conflicts)} "
            f"decision_points:{len(decision_points)}"
        )
        print(f"emergencies={emergency or []}")
        print(
            "Enter command as JSON action object, e.g. "
            '{"aircraft":"AAL123","type":"assign_heading","heading":270} ' 
            "or 'no_op'."
        )

    def _readline_with_timeout(self) -> str | None:
        ready, _, _ = select.select([sys.stdin], [], [], self._timeout_sec)
        if not ready:
            return None
        line = sys.stdin.readline()
        return line if line != "" else None

    def act(self, observation: dict) -> dict:
        self._summarize(observation)
        for attempt in range(1, self._max_retries + 1):
            print(f"[attempt {attempt}/{self._max_retries}] > ", end="", flush=True)
            line = self._readline_with_timeout()
            if line is None:
                print(f"\nInput timeout ({self._timeout_sec:.1f}s). Falling back to no_op.")
                return {"actions": []}

            text = line.strip()
            if not text:
                print("Empty input. Please enter JSON action or 'no_op'.")
                continue
            if text == "no_op":
                return {"actions": [{"type": "no_op"}]}
            try:
                action = json.loads(text)
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON: {exc}. Try again.")
                continue
            if not isinstance(action, dict):
                print("Command must be a JSON object. Try again.")
                continue
            return {"actions": [action]}

        print("Max retries exceeded. Falling back to no_op.")
        return {"actions": []}
