"""A small Switch agent-protocol client: SSE down, HTTP up. Standard library only.

Follows docs/official/internals/agent-protocol.md of the Switch repository:
- register once with a registration key (`POST /agents`), keep the API key;
- open `GET /agents/{id}/events` as an event stream with a connection id we
  generate and reuse, declaring the room on the URL;
- beat `POST /agents/{id}/connection/beat` every 2 seconds, and reopen on
  404 / 409 instead of backing off;
- call operations at `POST /agents/{id}/ops/{name}` with the connection id
  header, so they act in the room the connection holds.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

log = logging.getLogger("proctor.switch")

CLIENT_NAME = "bandup-proctor"
CLIENT_VERSION = "1.0.0"


class SwitchError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass
class Frame:
    event: str
    data: dict[str, Any]
    id: str | None


class SwitchClient:
    def __init__(self, base_url: str, agent_id: str = "", api_key: str = "", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key
        self.timeout = timeout
        self._ssl = ssl.create_default_context()

    # ── plumbing ──

    def _request(self, method: str, path: str, body: Any = None, token: str | None = None,
                 headers: dict[str, str] | None = None, timeout: float | None = None) -> Any:
        url = self.base_url + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        bearer = token if token is not None else self.api_key
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self._ssl) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise SwitchError(e.code, detail) from None

    # ── public routes ──

    def health(self) -> Any:
        return self._request("GET", "/health", token="")

    def register(self, registration_key: str, name: str, display_name: str, description: str,
                 overwrite: bool = False) -> dict[str, str]:
        """Register a standalone, always-on agent. The API key comes back once."""
        body = {
            "name": name,
            "display_name": display_name,
            "description": description,
            "connector_type": CLIENT_NAME,
            "integration_profile": {
                # Reachable whenever this process runs, in every room it is in.
                "connection_model": "always_on",
                "message_exchange": True,
                "pre_invocation_mediation": [],
                "post_invocation_mediation": [],
                "event_reporting": [],
            },
            "tools": [],
            "models": [],
            "metadata": {"client": CLIENT_NAME, "client_version": CLIENT_VERSION},
            "overwrite": overwrite,
        }
        return self._request("POST", "/agents", body, token=registration_key)

    # ── operations ──

    def op(self, name: str, args: dict[str, Any] | None = None, connection_id: str | None = None) -> Any:
        headers = {"X-Switch-Connection-Id": connection_id} if connection_id else None
        res = self._request("POST", f"/agents/{self.agent_id}/ops/{name}", args or {}, headers=headers)
        return res.get("result") if isinstance(res, dict) else res

    def list_rooms(self) -> list[dict[str, Any]]:
        return self.op("list_rooms") or []

    def post_message(self, room_id: str, body: str, connection_id: str | None = None) -> str:
        """Post to a room. With a connection bound to the room, through the op;
        otherwise through the connection-less message route."""
        if connection_id:
            res = self.op("post_message", {"body": body}, connection_id)
            return (res or {}).get("event_id", "")
        res = self._request("POST", f"/agents/{self.agent_id}/message", {"room_id": room_id, "content": body})
        return (res or {}).get("event_id", "")

    def create_room_document(self, connection_id: str, name: str, description: str,
                             instructions: str, content: str) -> str:
        res = self.op("create_room_document", {
            "name": name, "description": description,
            "instructions": instructions, "content": content,
        }, connection_id)
        return (res or {}).get("document_id") or (res or {}).get("id", "")

    # ── the connection ──

    def beat(self, connection_id: str, generation: int | None, cursor: int | None) -> Any:
        body: dict[str, Any] = {"connection_id": connection_id}
        if generation is not None:
            body["generation"] = generation
        if cursor is not None:
            body["cursor"] = cursor
        return self._request("POST", f"/agents/{self.agent_id}/connection/beat", body, timeout=5)

    def stream(self, connection_id: str, room_id: str, last_event_id: str | None = None) -> Iterator[Frame]:
        """Open the event stream for one room and yield frames until it closes."""
        q = (f"connection_id={connection_id}&scope=single&filter=all&rooms={room_id}"
             f"&client={CLIENT_NAME}&client_version={CLIENT_VERSION}")
        if not last_event_id:
            q += "&start_from=head"
        req = urllib.request.Request(f"{self.base_url}/agents/{self.agent_id}/events?{q}")
        req.add_header("Accept", "text/event-stream")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        if last_event_id:
            req.add_header("Last-Event-ID", last_event_id)
        try:
            # The server writes a keepalive comment every 15 s, so 60 s of
            # silence means the stream is dead.
            resp = urllib.request.urlopen(req, timeout=60, context=self._ssl)
        except urllib.error.HTTPError as e:
            raise SwitchError(e.code, e.read().decode("utf-8", "replace")[:500]) from None
        with resp:
            event, data_lines, frame_id = "message", [], None
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        try:
                            data = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            data = {"raw": "\n".join(data_lines)}
                        yield Frame(event, data if isinstance(data, dict) else {"value": data}, frame_id)
                    event, data_lines, frame_id = "message", [], None
                elif line.startswith(":"):
                    # A keepalive comment. Surfaced so the caller can notice a
                    # failed heartbeat even while the room is quiet.
                    yield Frame("keepalive", {}, None)
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line.startswith("id:"):
                    frame_id = line[3:].strip()
