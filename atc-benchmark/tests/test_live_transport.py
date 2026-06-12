import asyncio
import json

from atc_benchmark.server.live_transport import LiveTransportServer, create_live_asgi_app


def test_unsupported_websocket_envelope_type_gets_command_nack():
    async def run_app():
        messages = iter(
            [
                {"type": "websocket.receive", "text": json.dumps({"type": "unsupported_live_action"})},
                {"type": "websocket.disconnect"},
            ]
        )
        sent = []
        app = create_live_asgi_app(LiveTransportServer())

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        await app({"type": "websocket", "path": "/live"}, receive, send)
        return sent

    sent = asyncio.run(run_app())
    websocket_frames = [message for message in sent if message["type"] == "websocket.send"]
    assert len(websocket_frames) == 1

    payload = json.loads(websocket_frames[0]["text"])
    assert payload["type"] == "command_ack"
    assert payload["ok"] is False
    assert payload["status"] == "nack"
    assert payload["reason"] == "unsupported_envelope_type"
    assert payload["details"] == {"envelope_type": "unsupported_live_action"}
