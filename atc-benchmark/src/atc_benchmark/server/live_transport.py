from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TickEnvelope:
    """Live envelope shape consumed by viewer.handleLiveEnvelope."""

    tick: dict[str, Any]
    type: str = "tick"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "tick": self.tick}


class LiveTransportServer:
    """Broadcasts simulation ticks to websocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._latest_tick_envelope: dict[str, Any] | None = None

    async def subscribe_tick_stream(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        if self._latest_tick_envelope is not None:
            await queue.put(self._latest_tick_envelope)
        return queue

    def unsubscribe_tick_stream(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish_envelope(self, envelope: dict[str, Any]) -> None:
        if envelope.get("type") == "tick":
            self._latest_tick_envelope = envelope
        for subscriber in list(self._subscribers):
            await subscriber.put(envelope)

    async def publish_tick_snapshot(
        self,
        *,
        tick_id: int,
        time_sec: int,
        state: dict[str, Any],
        **extra: Any,
    ) -> None:
        payload = {
            "tick_id": tick_id,
            "time": time_sec,
            "state": state,
            **extra,
        }
        envelope = TickEnvelope(tick=payload).to_dict()
        await self.publish_envelope(envelope)


CommandHandler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


def create_live_asgi_app(live_server: LiveTransportServer, *, on_command: CommandHandler | None = None):
    """Create a minimal ASGI app that serves ws://.../live.

    The client must send {"type": "subscribe_tick_stream"} to start receiving
    tick envelopes.
    """

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/live":
            body = b"ATC live endpoint requires a WebSocket connection.\n"
            await send(
                {
                    "type": "http.response.start",
                    "status": 426,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"upgrade", b"websocket"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        if scope["type"] != "websocket" or scope.get("path") != "/live":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            elif scope["type"] == "http":
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b""})
            return

        await send({"type": "websocket.accept"})
        queue: asyncio.Queue[dict[str, Any]] | None = None
        forward_task: asyncio.Task[Any] | None = None

        async def forward_ticks(tick_queue: asyncio.Queue[dict[str, Any]]) -> None:
            while True:
                envelope = await tick_queue.get()
                await send({"type": "websocket.send", "text": json.dumps(envelope)})

        try:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message["type"] != "websocket.receive":
                    continue

                text = message.get("text") or "{}"
                payload = json.loads(text)
                if payload.get("type") == "subscribe_tick_stream" and queue is None:
                    queue = await live_server.subscribe_tick_stream()
                    forward_task = asyncio.create_task(forward_ticks(queue))
                    continue
                if payload.get("type") in {"command", "pause", "resume", "reset", "end_session"} and on_command is not None:
                    response = on_command(payload)
                    if asyncio.iscoroutine(response):
                        response = await response
                    await send({"type": "websocket.send", "text": json.dumps(response)})
        finally:
            if forward_task is not None:
                forward_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await forward_task
            if queue is not None:
                live_server.unsubscribe_tick_stream(queue)

    return app
