from __future__ import annotations

from math import cos, radians
from statistics import median
from typing import Any

from .models import FetchedTrafficWindow, FlightTrack, ScenarioSeedConfig

NM_PER_LAT_DEG = 60.0


def latlon_to_local_nm(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    y_nm = (lat - origin_lat) * NM_PER_LAT_DEG
    x_nm = (lon - origin_lon) * NM_PER_LAT_DEG * cos(radians(origin_lat))
    return x_nm, y_nm


def _infer_role(track: FlightTrack, x_nm: float, y_nm: float) -> tuple[str, str, float]:
    if not track.points:
        return "arrival", "airborne", 0.0
    last = track.points[-1]
    if last.on_ground:
        return "departure", "waiting_departure", 0.7
    if last.vertical_rate_fpm < -200 or (x_nm * x_nm + y_nm * y_nm) < 36:
        return "arrival", "airborne", 0.8
    if last.vertical_rate_fpm > 200:
        return "departure", "airborne_departure", 0.8
    return "arrival", "airborne", 0.5


def build_scenario(window: FetchedTrafficWindow, config: ScenarioSeedConfig) -> dict[str, Any]:
    req = window.request
    selected_ts = min((p.timestamp for t in window.tracks for p in t.points), default=req.start_time)
    aircraft: list[dict[str, Any]] = []
    confidences = []
    source_ids = []
    for idx, track in enumerate(window.tracks, start=1):
        if not track.points:
            continue
        p = min(track.points, key=lambda point: abs((point.timestamp - selected_ts).total_seconds()))
        x_nm, y_nm = latlon_to_local_nm(p.lat, p.lon, req.airport_lat, req.airport_lon)
        role, status, confidence = _infer_role(track, x_nm, y_nm)
        confidences.append(confidence)
        source_ids.append(track.provider_ids)
        callsign = (track.callsign or f"{window.provider.upper()}{idx:03d}").strip()
        aircraft.append(
            {
                "callsign": callsign,
                "role": role,
                "x_nm": round(x_nm, 3),
                "y_nm": round(y_nm, 3),
                "altitude_ft": max(0.0, float(p.altitude_ft)),
                "speed_kt": max(0.0, float(p.ground_speed_kt)),
                "heading_deg": float(p.track_deg) % 360,
                "vertical_rate_fpm": float(p.vertical_rate_fpm),
                "status": status,
                "target_runway": config.default_target_runway or config.active_runway,
                "clearance": None,
                "emergency": False,
            }
        )

    return {
        "tick_sec": config.tick_sec,
        "airport": {
            "runway_id": config.runway_id,
            "active_runway": config.active_runway,
            "departure_queue": [a["callsign"] for a in aircraft if a["role"] == "departure" and a["status"] == "waiting_departure"],
        },
        "aircraft": aircraft,
        "scenario_metadata": {
            "provider": window.provider,
            "airport": req.airport_icao,
            "window_start": req.start_time.isoformat(),
            "window_end": req.end_time.isoformat(),
            "selected_timestamp": selected_ts.isoformat(),
            "inference_confidence": median(confidences) if confidences else 0.0,
            "source_flight_ids": source_ids,
        },
    }
