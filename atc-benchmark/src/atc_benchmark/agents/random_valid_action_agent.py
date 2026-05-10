from __future__ import annotations

import random


class RandomValidActionAgent:
    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def act(self, observation: dict) -> dict:
        aircraft = list(observation["snapshot"]["aircraft"].keys())
        if not aircraft:
            return {"actions": []}
        cs = self._rng.choice(aircraft)
        speed = self._rng.choice([140, 180, 220, 260])
        heading = self._rng.randrange(0, 360)
        actions = [
            {"aircraft": cs, "type": "assign_speed", "speed_kt": speed},
            {"aircraft": cs, "type": "assign_heading", "heading": heading},
        ]
        return {"actions": [self._rng.choice(actions)]}
