"""The proctor's command line: register it, run it against Switch, or try it offline.

    python -m bandup_proctor register --url http://localhost:PORT --key <registration key>
    python -m bandup_proctor run
    python -m bandup_proctor simulate --scale 0.02
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import uuid
from pathlib import Path

from .engine import Action, Config, FileAnswer, Inbound, Post, Proctor
from .switch_client import Frame, SwitchClient, SwitchError

log = logging.getLogger("proctor")
ENV_FILE = Path(os.environ.get("PROCTOR_ENV", ".env"))

# ── settings ────────────────────────────────────────────────────────────────


def load_env(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines into os.environ without overriding what is set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def write_env(values: dict[str, str], path: Path = ENV_FILE) -> None:
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    existing.update(values)
    path.write_text("".join(f"{k}={v}\n" for k, v in existing.items()), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def engine_config() -> Config:
    return Config(
        name=os.environ.get("PROCTOR_NAME", "proctor"),
        examiner=os.environ.get("EXAMINER_ALIAS", "examiner"),
        speaking=os.environ.get("SPEAKING_ALIAS", "speaking"),
        module=os.environ.get("PROCTOR_MODULE", "Academic"),
        time_scale=float(os.environ.get("PROCTOR_TIME_SCALE", "1")),
        grace_seconds=float(os.environ.get("PROCTOR_GRACE_SECONDS", "30")),
    )


class StateFile:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"counter": 0, "seen": {}, "connections": {}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                log.warning("state file %s is unreadable; starting fresh", path)

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def connection_id(self, room_id: str) -> str:
        """One connection id per room, generated once and reused on every reconnect."""
        conns = self.data.setdefault("connections", {})
        if room_id not in conns:
            conns[room_id] = str(uuid.uuid4())
            self.save()
        return conns[room_id]


# ── register ────────────────────────────────────────────────────────────────


def cmd_register(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("SWITCH_URL", "")
    key = args.key or os.environ.get("SWITCH_REGISTRATION_KEY", "")
    if not url or not key:
        print("Need --url (the Switch server URL) and --key (a registration key).", file=sys.stderr)
        return 2
    client = SwitchClient(url)
    try:
        client.health()
    except (SwitchError, urllib.error.URLError, OSError) as e:
        print(f"{url}/health did not answer ({e}). Is this the server URL Switch Console shows?", file=sys.stderr)
        return 1
    try:
        res = client.register(key, args.name, args.display_name,
                              "Times IELTS practice tasks and hands answers to the markers.",
                              overwrite=args.overwrite)
    except SwitchError as e:
        if e.status == 409:
            print(f"An agent named {args.name} already exists. Pass --overwrite to replace it.", file=sys.stderr)
        elif e.status == 401:
            print("The registration key was refused. Create one in Switch Console: Settings, API keys.",
                  file=sys.stderr)
        else:
            print(f"Registration failed: {e}", file=sys.stderr)
        return 1
    write_env({"SWITCH_URL": url, "PROCTOR_AGENT_ID": res["id"], "PROCTOR_API_KEY": res["api_key"],
               "PROCTOR_NAME": args.name})
    print(f"Registered {args.name} (id {res['id']}). The API key is saved in {ENV_FILE} and is shown only once.")
    print("Next: in Switch Console, let your study buddies address it (the agent's page, who can address it),")
    print("then run `python -m bandup_proctor run` and use templates/bandup.template.yaml.")
    return 0


# ── run ─────────────────────────────────────────────────────────────────────


class Runner:
    def __init__(self, client: SwitchClient, engine: Proctor, state: StateFile, rooms_filter: set[str]):
        self.client = client
        self.engine = engine
        self.state = state
        self.rooms_filter = rooms_filter
        self.lock = threading.Lock()
        self.workers: dict[str, RoomWorker] = {}
        self.own_ids: set[str] = set()
        self.stop = threading.Event()

    def own(self, payload: dict) -> bool:
        if payload.get("message_id") in self.own_ids:
            return True
        name = (payload.get("sender_name") or "").lower()
        return name in {self.engine.config.name.lower(), "proctor"}

    def on_message(self, room_id: str, payload: dict) -> None:
        if self.own(payload) or not payload.get("body"):
            return
        msg = Inbound(
            room_id=room_id,
            sender=payload.get("sender", ""),
            sender_name=payload.get("sender_name") or "you",
            body=payload.get("body", ""),
            addressed=bool(payload.get("addressed")),
            message_id=payload.get("message_id", ""),
        )
        with self.lock:
            actions = self.engine.handle(msg, time.time())
        self.execute(actions)

    def tick_forever(self) -> None:
        while not self.stop.wait(1.0):
            with self.lock:
                actions = self.engine.tick(time.time())
            if actions:
                self.execute(actions)

    def execute(self, actions: list[Action]) -> None:
        for action in actions:
            worker = self.workers.get(action.room_id)
            conn = worker.connection_id if worker and worker.live.is_set() else None
            try:
                if isinstance(action, Post):
                    self._post(action.room_id, action.body, conn)
                elif isinstance(action, FileAnswer):
                    self._file(action, conn)
            except Exception as e:  # never let one failed post kill the proctor
                log.exception("action failed in %s: %s", action.room_id, e)
        self.state.save()  # the engine's state is the state file's dict

    def _post(self, room_id: str, body: str, conn: str | None) -> None:
        try:
            event_id = self.client.post_message(room_id, body, conn)
        except SwitchError as e:
            if conn and e.status in (400, 409):
                event_id = self.client.post_message(room_id, body, None)
            else:
                raise
        if event_id:
            self.own_ids.add(event_id)

    def _file(self, action: FileAnswer, conn: str | None) -> None:
        doc_id = ""
        if conn:
            try:
                doc_id = self.client.create_room_document(conn, action.name, action.description,
                                                          action.instructions, action.content)
            except SwitchError as e:
                log.error("could not file %s as a room document: %s", action.name, e)
        if doc_id:
            self._post(action.room_id, action.handoff.replace("{doc_id}", doc_id), conn)
        else:
            self._post(action.room_id, action.fallback, conn)

    def discover(self) -> None:
        """Start a worker for every room the proctor is in (and the filter allows)."""
        try:
            rooms = self.client.list_rooms()
        except (SwitchError, urllib.error.URLError, OSError) as e:
            log.warning("could not list rooms: %s", e)
            return
        for room in rooms:
            rid, name = room.get("room_id"), room.get("name", "")
            if not rid or room.get("archived") or rid in self.workers:
                continue
            if self.rooms_filter and name not in self.rooms_filter and rid not in self.rooms_filter:
                continue
            worker = RoomWorker(self, rid, name, self.state.connection_id(rid))
            self.workers[rid] = worker
            worker.start()
            log.info("watching room %s (%s)", name, rid)
        if not self.workers:
            log.warning("the proctor is in no rooms yet: run the BandUp template, or add it to a room")

    def run(self) -> None:
        threading.Thread(target=self.tick_forever, daemon=True, name="tick").start()
        while not self.stop.is_set():
            self.discover()
            self.stop.wait(60)


class RoomWorker(threading.Thread):
    """One room, one connection: stream reader plus its heartbeat."""

    def __init__(self, runner: Runner, room_id: str, room_name: str, connection_id: str):
        super().__init__(daemon=True, name=f"room-{room_name}")
        self.runner, self.room_id, self.room_name = runner, room_id, room_name
        self.connection_id = connection_id
        self.generation: int | None = None
        self.cursor: int | None = None
        self.last_event_id: str | None = None
        self.live = threading.Event()
        self.dead = threading.Event()

    def beat_forever(self) -> None:
        while self.live.is_set() and not self.runner.stop.is_set():
            try:
                self.runner.client.beat(self.connection_id, self.generation, self.cursor)
            except SwitchError as e:
                if e.status in (404, 409):
                    log.info("%s: heartbeat refused (%s), reopening", self.room_name, e.status)
                    self.dead.set()
                    return
                log.warning("%s: heartbeat failed: %s", self.room_name, e)
            except (urllib.error.URLError, OSError, socket.timeout) as e:
                log.warning("%s: heartbeat failed: %s", self.room_name, e)
            time.sleep(2)

    def run(self) -> None:
        backoff = 1.0
        while not self.runner.stop.is_set():
            self.dead.clear()
            try:
                for frame in self.runner.client.stream(self.connection_id, self.room_id, self.last_event_id):
                    backoff = 1.0
                    if not self.handle(frame) or self.dead.is_set():
                        break
            except SwitchError as e:
                if e.status == 403:
                    log.error("%s: not a member any more, stopping", self.room_name)
                    self.runner.workers.pop(self.room_id, None)
                    return
                log.warning("%s: stream refused: %s", self.room_name, e)
            except (urllib.error.URLError, OSError, socket.timeout) as e:
                log.warning("%s: stream dropped: %s", self.room_name, e)
            self.live.clear()
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def handle(self, frame: Frame) -> bool:
        if frame.event == "connection_state":
            self.generation = frame.data.get("generation")
            self.cursor = frame.data.get("cursor")
            interval = frame.data.get("heartbeat_interval_seconds")
            log.info("%s: connected (cursor %s, heartbeat %ss)", self.room_name, self.cursor, interval)
            if not self.live.is_set():
                self.live.set()
                threading.Thread(target=self.beat_forever, daemon=True, name=f"beat-{self.room_name}").start()
            return True
        if frame.event == "evicted":
            log.warning("%s: evicted (%s)", self.room_name, frame.data.get("reason"))
            return False
        if frame.event == "gap":
            log.warning("%s: gap in events from %s", self.room_name, frame.data.get("from_sequence"))
            return True
        if frame.id:
            self.last_event_id = frame.id
            seq = frame.data.get("sequence")
            if isinstance(seq, int):
                self.cursor = seq
        if frame.event == "message" and frame.data.get("room_id", self.room_id) == self.room_id:
            self.runner.on_message(self.room_id, frame.data.get("payload") or {})
        return True


def cmd_run(args: argparse.Namespace) -> int:
    url = os.environ.get("SWITCH_URL", "")
    agent_id = os.environ.get("PROCTOR_AGENT_ID", "")
    api_key = os.environ.get("PROCTOR_API_KEY", "")
    if not (url and agent_id and api_key):
        print(f"SWITCH_URL, PROCTOR_AGENT_ID and PROCTOR_API_KEY must be set (in {ENV_FILE}). "
              "Run `python -m bandup_proctor register` first.", file=sys.stderr)
        return 2
    client = SwitchClient(url, agent_id, api_key)
    try:
        client.health()
    except (SwitchError, urllib.error.URLError, OSError) as e:
        print(f"{url}/health did not answer: {e}", file=sys.stderr)
        return 1
    state = StateFile(Path(os.environ.get("PROCTOR_STATE", "proctor-state.json")))
    engine = Proctor(engine_config(), state=state.data, today=lambda: dt.date.today().isoformat())
    rooms = {r.strip() for r in os.environ.get("PROCTOR_ROOMS", "").split(",") if r.strip()}
    log.info("proctor %s starting against %s (time scale %s)", engine.config.name, url, engine.config.time_scale)
    try:
        Runner(client, engine, state, rooms).run()
    except KeyboardInterrupt:
        pass
    return 0


# ── simulate ────────────────────────────────────────────────────────────────


def cmd_simulate(args: argparse.Namespace) -> int:
    """Play a room in the terminal. Lines are `name: message`; time runs for real."""
    cfg = engine_config()
    cfg.time_scale = args.scale
    cfg.grace_seconds = 2
    engine = Proctor(cfg, today=lambda: dt.date.today().isoformat())
    stop = threading.Event()
    docs = 0

    def show(actions: list[Action]) -> None:
        nonlocal docs
        for a in actions:
            if isinstance(a, Post):
                print(f"\n[proctor] {a.body}\n")
            else:
                docs += 1
                print(f"\n[switch] room document created: {a.name}\n{a.content}")
                print(f"[proctor] {a.handoff.replace('{doc_id}', f'doc-{docs}')}\n")
        sys.stdout.flush()

    def ticker() -> None:
        while not stop.wait(0.5):
            show(engine.tick(time.time()))

    threading.Thread(target=ticker, daemon=True).start()
    print(f"Offline room. Type `name: message`, e.g. `omar: @{cfg.name} writing task 2`. Ctrl-D to quit.")
    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = re.match(r"^\s*(\w+)\s*:\s*(.*)$", line)
            who, body = (m.group(1), m.group(2)) if m else ("you", line.strip())
            msg = Inbound("sim-room", who.lower(), who.capitalize(), body, f"@{cfg.name}" in body.lower())
            show(engine.handle(msg, time.time()))
    finally:
        stop.set()
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="bandup_proctor", description="BandUp proctor, a Switch protocol agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="register the proctor with a Switch server")
    r.add_argument("--url", help="Switch server URL, e.g. http://localhost:51234")
    r.add_argument("--key", help="registration key from Switch Console: Settings, API keys")
    r.add_argument("--name", default="proctor")
    r.add_argument("--display-name", default="Proctor")
    r.add_argument("--overwrite", action="store_true")
    r.set_defaults(fn=cmd_register)
    sub.add_parser("run", help="connect and serve every room the proctor is in").set_defaults(fn=cmd_run)
    s = sub.add_parser("simulate", help="try the proctor offline in the terminal")
    s.add_argument("--scale", type=float, default=0.02, help="time scale: 0.02 makes Task 2 48 seconds")
    s.set_defaults(fn=cmd_simulate)
    args = p.parse_args(argv)
    return args.fn(args)
