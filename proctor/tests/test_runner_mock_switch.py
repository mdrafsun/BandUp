"""The runner against a fake Switch agent bridge, over real HTTP and SSE.

The fake speaks the slice of the agent protocol the proctor uses: /health,
POST /agents, ops (list_rooms, post_message, create_room_document), the
event stream and the heartbeat. It checks the proctor's requests carry what
the protocol asks for (bearer key, connection id header, the room declared
on the stream URL) and plays a learner through one timed task.
"""

import json
import queue
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from bandup_proctor.cli import Runner, StateFile
from bandup_proctor.engine import Config, PromptBank, Proctor
from bandup_proctor.switch_client import SwitchClient

AGENT = "agent-123"
KEY = "sk-agent"
ROOM = "room-practice"


class FakeSwitch:
    def __init__(self):
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.posts: list[str] = []
        self.docs: list[dict] = []
        self.beats = 0
        self.stream_queries: list[dict] = []
        self.op_headers: list[str] = []
        self.seq = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                url = urlparse(self.path)
                if url.path == "/health":
                    return self._json(200, {"status": "ok"})
                if url.path == f"/agents/{AGENT}/events":
                    assert self.headers["Authorization"] == f"Bearer {KEY}"
                    assert "text/event-stream" in self.headers["Accept"]
                    fake.stream_queries.append(parse_qs(url.query))
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    state = {"connection_id": parse_qs(url.query)["connection_id"][0], "agent_id": AGENT,
                             "rooms": [ROOM], "cursor": 0, "generation": 3, "heartbeat_interval_seconds": 2}
                    self.wfile.write(f"event: connection_state\ndata: {json.dumps(state)}\n\n".encode())
                    self.wfile.flush()
                    deadline = time.time() + 20
                    while time.time() < deadline:
                        try:
                            payload = fake.events.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        fake.seq += 1
                        frame = {"type": "message", "room_id": ROOM, "payload": payload, "sequence": fake.seq}
                        self.wfile.write(f"id: {fake.seq}\nevent: message\ndata: {json.dumps(frame)}\n\n".encode())
                        self.wfile.flush()
                    return
                self._json(404, {"detail": "nope"})

            def do_POST(self):
                url = urlparse(self.path)
                body = self._body()
                if url.path == "/agents":
                    assert self.headers["Authorization"] == "Bearer reg-key"
                    assert body["integration_profile"]["connection_model"] == "always_on"
                    return self._json(200, {"id": AGENT, "api_key": KEY})
                if url.path == f"/agents/{AGENT}/connection/beat":
                    assert body.get("generation") == 3
                    fake.beats += 1
                    return self._json(200, {"ok": True, "rooms": [ROOM], "cursor": 0})
                if url.path.startswith(f"/agents/{AGENT}/ops/"):
                    op = url.path.rsplit("/", 1)[1]
                    fake.op_headers.append(self.headers.get("X-Switch-Connection-Id") or "")
                    if op == "list_rooms":
                        return self._json(200, {"result": [{"room_id": ROOM, "name": "bandup-practice"}]})
                    if op == "post_message":
                        fake.posts.append(body["body"])
                        return self._json(200, {"result": {"event_id": f"ev-{len(fake.posts)}"}})
                    if op == "create_room_document":
                        fake.docs.append(body)
                        return self._json(200, {"result": {"document_id": "doc-9"}})
                return self._json(404, {"detail": "nope"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def say(self, body, addressed=False, sender="@switch-user-omar:local", name="Omar"):
        self.events.put({"addressed": addressed, "sender": sender, "sender_name": name,
                         "message_id": f"m-{time.time()}", "body": body, "timestamp": 0, "attachments": []})

    def wait_for(self, check, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            if check():
                return True
            time.sleep(0.05)
        return False


class RunnerAgainstFakeSwitch(unittest.TestCase):
    def test_register(self):
        fake = FakeSwitch()
        res = SwitchClient(fake.url).register("reg-key", "proctor", "Proctor", "times tasks")
        self.assertEqual(res, {"id": AGENT, "api_key": KEY})
        fake.server.shutdown()

    def test_timed_task_end_to_end(self):
        fake = FakeSwitch()
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.mkdtemp()) / "state.json"
        state = StateFile(tmp)
        engine = Proctor(Config(), bank=PromptBank.load(seed=3), state=state.data, today=lambda: "2026-11-06")
        runner = Runner(SwitchClient(fake.url, AGENT, KEY), engine, state, set())
        threading.Thread(target=runner.run, daemon=True).start()

        self.assertTrue(fake.wait_for(lambda: ROOM in runner.workers and runner.workers[ROOM].live.is_set()))
        q = fake.stream_queries[0]
        self.assertEqual(q["rooms"], [ROOM])
        self.assertEqual(q["scope"], ["single"])

        fake.say("@proctor writing task 2", addressed=True)
        self.assertTrue(fake.wait_for(lambda: any("Writing Task 2" in p for p in fake.posts)))
        fake.say("ESSAY: " + " ".join(["word"] * 260) + "\nDONE")
        self.assertTrue(fake.wait_for(lambda: any(p.startswith("@examiner") for p in fake.posts)))

        self.assertEqual(len(fake.docs), 1)
        self.assertIn("260 words", fake.docs[0]["content"])
        handoff = [p for p in fake.posts if p.startswith("@examiner")][0]
        self.assertIn("(id doc-9)", handoff)
        conn = runner.workers[ROOM].connection_id
        self.assertTrue(all(h == conn for h in fake.op_headers[1:]))  # every op after list_rooms is bound
        self.assertTrue(fake.wait_for(lambda: fake.beats >= 1, timeout=4))
        self.assertEqual(json.loads(tmp.read_text())["counter"], 1)

        # The proctor's own messages coming back on the stream are ignored.
        before = len(fake.posts)
        fake.say("@proctor help", addressed=True, name="proctor")
        time.sleep(0.5)
        self.assertEqual(len(fake.posts), before)
        runner.stop.set()
        fake.server.shutdown()


if __name__ == "__main__":
    unittest.main()
