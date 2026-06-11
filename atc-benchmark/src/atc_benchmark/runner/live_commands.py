from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atc_benchmark.simulator.engine import SimulationStepper


@dataclass(slots=True)
class CommandAck:
    ok: bool
    status: str
    transport: str
    reason: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "command_ack",
            "ok": self.ok,
            "status": self.status,
            "transport": self.transport,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.details:
            payload["details"] = self.details
        return payload


def _nack(transport: str, reason: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return CommandAck(ok=False, status="nack", transport=transport, reason=reason, details=details).to_dict()


def _extract_command_from_envelope(payload: dict[str, Any], *, require_session: bool) -> tuple[dict[str, Any] | None, str | None]:
    if payload.get("type") != "command":
        return None, "unsupported_envelope_type"
    if require_session and not payload.get("session_id"):
        return None, "missing_session_id"
    command = payload.get("command")
    if not isinstance(command, dict):
        return None, "missing_command"
    return command, None


def process_command(stepper: SimulationStepper, command: dict[str, Any], *, transport: str) -> dict[str, Any]:
    """Validate one live command and enqueue it for execution at the next tick.

    The command is read back immediately (ack/nack) but executes with the same
    pilot readback delay used in batch runs.
    """
    enqueued, invalid = stepper.submit_command(command)
    if invalid is not None:
        return _nack(
            transport,
            invalid.get("reason", "invalid_command"),
            details={"rejected_action": invalid.get("action", command)},
        )
    details: dict[str, Any] = {"accepted_action": command}
    if enqueued is not None:
        details["issued_at_sec"] = enqueued["issued_at_sec"]
        details["scheduled_execution_time_sec"] = enqueued["scheduled_execution_time_sec"]
    return CommandAck(ok=True, status="ack", transport=transport, details=details).to_dict()


def handle_ws_envelope(stepper: SimulationStepper, payload: dict[str, Any]) -> dict[str, Any]:
    command, error = _extract_command_from_envelope(payload, require_session=True)
    if error:
        return _nack("websocket", error)
    assert command is not None
    return process_command(stepper, command, transport="websocket")


def handle_http_command(stepper: SimulationStepper, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("type") == "command":
        command, error = _extract_command_from_envelope(payload, require_session=False)
        if error:
            return _nack("http", error)
        assert command is not None
        return process_command(stepper, command, transport="http")
    if isinstance(payload.get("command"), dict):
        return process_command(stepper, payload["command"], transport="http")
    if isinstance(payload, dict) and "type" in payload and "aircraft" in payload:
        return process_command(stepper, payload, transport="http")
    return _nack("http", "missing_command")
