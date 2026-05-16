from __future__ import annotations

from typing import Protocol

from .models import AirportSnapshotRequest, FetchedTrafficWindow


class TrafficDataProvider(Protocol):
    def fetch_tracks(self, request: AirportSnapshotRequest) -> FetchedTrafficWindow:
        ...
