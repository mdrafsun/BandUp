"""The proctor's logic, with no network in it.

The engine is fed room messages (`handle`) and the passing of time (`tick`)
and answers with actions for the runner to carry out: post a message, or
file an answer as a room document and then post a hand-off naming it. Time
is passed in, never read, so every timer is testable without waiting.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ── What the engine is given and what it asks for ──────────────────────────


@dataclass(frozen=True)
class Inbound:
    """A room message, as the runner read it off the event stream."""

    room_id: str
    sender: str  # opaque participant id; matched, never parsed
    sender_name: str
    body: str
    addressed: bool
    message_id: str = ""


@dataclass(frozen=True)
class Post:
    room_id: str
    body: str


@dataclass(frozen=True)
class FileAnswer:
    """Create a room document holding an answer, then post `handoff`.

    `handoff` may contain `{doc_id}`, filled with the id Switch returns. When
    the document cannot be created the runner posts `fallback` instead, which
    carries the answer inline, so nothing is ever lost silently.
    """

    room_id: str
    name: str
    description: str
    instructions: str
    content: str
    handoff: str
    fallback: str


Action = Post | FileAnswer


# ── Tasks ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskKind:
    code: str  # W1, W2, S2
    label: str
    minutes: float  # answer time
    prep_minutes: float  # silent preparation before the clock starts (Part 2)
    min_words: int  # 0 = no minimum
    marker: str  # who marks it: "examiner" or "speaking"


TASKS: dict[str, TaskKind] = {
    "W1": TaskKind("W1", "Writing Task 1", 20, 0, 150, "examiner"),
    "W2": TaskKind("W2", "Writing Task 2", 40, 0, 250, "examiner"),
    "S2": TaskKind("S2", "Speaking Part 2", 2, 1, 0, "speaking"),
}

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ── Prompt bank ─────────────────────────────────────────────────────────────


class PromptBank:
    def __init__(self, data: dict[str, list[dict[str, str]]], rng: random.Random):
        self._data = {k: v for k, v in data.items() if not k.startswith("_")}
        self._rng = rng

    @classmethod
    def load(cls, path: Path | None = None, seed: int | None = None) -> "PromptBank":
        path = path or Path(__file__).with_name("prompts.json")
        return cls(json.loads(path.read_text(encoding="utf-8")), random.Random(seed))

    def find(self, prompt_id: str) -> tuple[str, dict[str, str]] | None:
        for bank, items in self._data.items():
            for item in items:
                if item["id"].lower() == prompt_id.lower():
                    return bank, item
        return None

    def pick(self, bank: str, avoid: set[str]) -> dict[str, str]:
        items = self._data[bank]
        fresh = [i for i in items if i["id"] not in avoid] or items
        return self._rng.choice(fresh)

    def ids(self, bank: str) -> list[str]:
        return [i["id"] for i in self._data.get(bank, [])]


# ── A running task ─────────────────────────────────────────────────────────


@dataclass
class Session:
    room_id: str
    owner: str
    owner_name: str
    kind: TaskKind
    prompt_id: str
    prompt: str
    created: float
    starts: float  # when the answer clock starts (after prep)
    ends: float
    scale: float
    collecting: bool = False
    parts: list[str] = field(default_factory=list)
    first_answer_at: float | None = None
    warned: bool = False
    announced_start: bool = False
    then: list[str] = field(default_factory=list)  # queued task codes (mock test)

    @property
    def total(self) -> float:
        return self.ends - self.starts


# ── The engine ─────────────────────────────────────────────────────────────

ANSWER_PREFIX = re.compile(r"^\s*(essay|answer)\s*[:\-]\s*", re.IGNORECASE)
# DONE on a line of its own, any case; or a capital DONE closing the last line.
# A lower-case "done." ending a sentence of the essay does not submit it.
DONE_LINE = re.compile(r"(?:(?:^|\n)\s*(?i:done)\s*[.!]?\s*$)|(?:\s+DONE\s*[.!]?\s*$)")


@dataclass
class Config:
    name: str = "proctor"  # how people address the proctor
    examiner: str = "examiner"  # alias of the writing examiner in the room
    speaking: str = "speaking"  # alias of the speaking partner
    module: str = "Academic"  # Academic | General Training, picks the Task 1 bank
    time_scale: float = 1.0  # 0.05 turns a 40 minute task into 2 minutes, for demos
    grace_seconds: float = 30.0  # after time is up, how long late text is still taken


class Proctor:
    def __init__(
        self,
        config: Config | None = None,
        bank: PromptBank | None = None,
        state: dict | None = None,
        today: Callable[[], str] | None = None,
    ):
        self.config = config or Config()
        self.bank = bank or PromptBank.load()
        # Persisted between runs by the runner: a submission counter, and the
        # prompts each person has had, so nobody gets the same one twice.
        self.state = state if state is not None else {"counter": 0, "seen": {}}
        self.sessions: dict[tuple[str, str], Session] = {}
        self._today = today or (lambda: "")

    # ── inbound messages ──

    def handle(self, msg: Inbound, now: float) -> list[Action]:
        key = (msg.room_id, msg.sender)
        session = self.sessions.get(key)
        if msg.addressed:
            command = self._strip_mention(msg.body)
            if session and command.lower().strip(" .!") == "done":
                return self._finish(session, now, reason="done")
            return self._command(msg, command, now)
        if session:
            return self._answer_text(session, msg.body, now)
        return []

    def _strip_mention(self, body: str) -> str:
        pattern = re.compile(rf"@{re.escape(self.config.name)}\b:?", re.IGNORECASE)
        return pattern.sub("", body).strip()

    def _command(self, msg: Inbound, text: str, now: float) -> list[Action]:
        t = text.lower().strip()
        key = (msg.room_id, msg.sender)
        session = self.sessions.get(key)
        minutes = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minutes)\b", t)
        if m:
            minutes = float(m.group(1))
        chosen = re.search(r"\b(w1a|w1g|w2|s2)-\d{2}\b", t)

        if t in ("", "help", "?", "hi", "hello"):
            return [Post(msg.room_id, self.help_text())]
        if t.startswith("status"):
            if not session:
                return [Post(msg.room_id, f"{msg.sender_name}, you have no task running.")]
            return [Post(msg.room_id, self._status(session, now))]
        if t.startswith(("cancel", "stop")):
            if not session:
                return [Post(msg.room_id, "Nothing to cancel.")]
            del self.sessions[key]
            return [Post(msg.room_id, f"Cancelled {session.kind.label} ({session.prompt_id}) for {msg.sender_name}.")]
        if t.startswith("prompts"):
            lines = [f"{bank}: {', '.join(self.bank.ids(bank))}" for bank in ("W1A", "W1G", "W2", "S2")]
            return [Post(msg.room_id, "Prompt ids you can ask for by name:\n" + "\n".join(lines))]

        if session:
            return [Post(msg.room_id, f"{msg.sender_name}, you already have {session.kind.label} running. "
                                      f"Send DONE to submit or `@{self.config.name} cancel`.")]

        if "mock" in t:
            return self._start(msg, "W1", now, minutes=None, then=["W2"])
        if re.search(r"\b(speaking|s)\s*(part)?\s*1\b|\bs1\b", t):
            return [Post(msg.room_id, f"@{self.config.speaking} please run Speaking Part 1 with "
                                      f"{msg.sender_name}: four or five questions, one at a time.")]
        if re.search(r"\b(speaking|s)\s*(part)?\s*3\b|\bs3\b", t):
            return [Post(msg.room_id, f"@{self.config.speaking} please run Speaking Part 3 with "
                                      f"{msg.sender_name}: four abstract questions, one at a time.")]
        if re.search(r"\b(speaking|s)\s*(part)?\s*2\b|\bs2\b|cue ?card", t):
            return self._start(msg, "S2", now, minutes, prompt_id=chosen.group(0) if chosen else None)
        if re.search(r"\b(task|t|w)\s*1\b|\bw1\b", t):
            return self._start(msg, "W1", now, minutes, prompt_id=chosen.group(0) if chosen else None,
                               general="gt" in t.split() or "general" in t)
        if re.search(r"\b(task|t|w)\s*2\b|\bw2\b|\bessay\b", t):
            return self._start(msg, "W2", now, minutes, prompt_id=chosen.group(0) if chosen else None)
        if chosen:
            code = {"w1a": "W1", "w1g": "W1", "w2": "W2", "s2": "S2"}[chosen.group(1)]
            return self._start(msg, code, now, minutes, prompt_id=chosen.group(0))
        return [Post(msg.room_id, f"I did not catch that. {self.help_text()}")]

    # ── starting a task ──

    def _start(self, msg: Inbound, code: str, now: float, minutes: float | None,
               prompt_id: str | None = None, general: bool = False,
               then: list[str] | None = None) -> list[Action]:
        kind = TASKS[code]
        if code == "W1":
            bank = "W1G" if (general or self.config.module.lower().startswith("general")) else "W1A"
        else:
            bank = code
        seen = set(self.state["seen"].get(msg.sender, []))
        if prompt_id:
            found = self.bank.find(prompt_id)
            if not found:
                return [Post(msg.room_id, f"There is no prompt {prompt_id}. Try `@{self.config.name} prompts`.")]
            bank, prompt = found
        else:
            prompt = self.bank.pick(bank, seen)
        self.state["seen"].setdefault(msg.sender, []).append(prompt["id"])

        scale = self.config.time_scale
        answer_minutes = minutes if minutes is not None else kind.minutes * scale
        prep = kind.prep_minutes * scale * 60
        starts = now + prep
        session = Session(
            room_id=msg.room_id, owner=msg.sender, owner_name=msg.sender_name, kind=kind,
            prompt_id=prompt["id"], prompt=prompt["text"], created=now, starts=starts,
            ends=starts + answer_minutes * 60, scale=scale, then=list(then or []),
            announced_start=prep == 0,
        )
        self.sessions[(msg.room_id, msg.sender)] = session
        return [Post(msg.room_id, self._prompt_text(session))]

    def _prompt_text(self, s: Session) -> str:
        total = clock(s.total)
        if s.kind.code == "S2":
            prep = clock(s.starts - s.created)
            return (
                f"🗣️ {s.kind.label} for {s.owner_name}, cue card {s.prompt_id}\n\n"
                f"{s.prompt}\n\n"
                f"You have {prep} to prepare, then {total} to speak. Dictate or type your answer "
                f"in messages starting with ANSWER: and send DONE when you finish."
            )
        words = f" Write at least {s.kind.min_words} words." if s.kind.min_words else ""
        mock = " This is part 1 of a full mock: Task 2 follows straight after." if s.then else ""
        return (
            f"📝 {s.kind.label} for {s.owner_name}, prompt {s.prompt_id}\n\n"
            f"{s.prompt}\n\n"
            f"⏱️ {total} starts now.{words}{mock} Start your first message with ESSAY: "
            f"(send as many messages as you like) and send DONE when you finish. "
            f"When time is up I collect what you have written."
        )

    def help_text(self) -> str:
        n = self.config.name
        return (
            f"I am the proctor: I set timed IELTS tasks and hand your answers to the markers.\n"
            f"• @{n} writing task 2   (or task 1, `task 1 gt` for General Training)\n"
            f"• @{n} speaking part 2   (cue card with prep time; parts 1 and 3 go to @{self.config.speaking})\n"
            f"• @{n} mock writing   (Task 1 then Task 2, one hour)\n"
            f"• add `25 min` to change the time, or a prompt id like W2-04 (`@{n} prompts` lists them)\n"
            f"• @{n} status · @{n} cancel\n"
            f"Answer in messages starting with ESSAY: (writing) or ANSWER: (speaking), then send DONE."
        )

    def _status(self, s: Session, now: float) -> str:
        if now < s.starts:
            return f"{s.owner_name}: preparing for {s.kind.label} ({s.prompt_id}), {clock(s.starts - now)} of prep left."
        words = count_words("\n".join(s.parts))
        return (f"{s.owner_name}: {s.kind.label} ({s.prompt_id}), {clock(s.ends - now)} left, "
                f"{words} words so far.")

    # ── answer text ──

    def _answer_text(self, s: Session, body: str, now: float) -> list[Action]:
        if now > s.ends + self.config.grace_seconds:
            return []
        text = body
        if not s.collecting:
            m = ANSWER_PREFIX.match(text)
            if not m:
                if text.strip().lower().strip(".!") == "done" and s.parts:
                    return self._finish(s, now, reason="done")
                return []  # chatter, not the answer
            s.collecting = True
            s.first_answer_at = now
            text = text[m.end():]
        else:
            m = ANSWER_PREFIX.match(text)
            if m:
                text = text[m.end():]
        finished = bool(DONE_LINE.search(text))
        if finished:
            text = DONE_LINE.sub("", text)
        if text.strip():
            s.parts.append(text.strip())
        if finished:
            return self._finish(s, now, reason="done")
        return []

    # ── time ──

    def tick(self, now: float) -> list[Action]:
        actions: list[Action] = []
        for s in list(self.sessions.values()):
            if not s.announced_start and now >= s.starts:
                s.announced_start = True
                actions.append(Post(s.room_id, f"⏱️ {s.owner_name}, preparation is over: start speaking now. "
                                               f"{clock(s.total)} on the clock."))
            warn_at = max(60.0 * s.scale, s.total * 0.25)
            if not s.warned and s.total >= 120 * s.scale and s.starts <= now and s.ends - now <= warn_at:
                s.warned = True
                actions.append(Post(s.room_id, f"⏳ {s.owner_name}: {clock(s.ends - now)} left on "
                                               f"{s.kind.label}."))
            if now >= s.ends + self.config.grace_seconds:
                actions += self._finish(s, now, reason="time")
        return actions

    # ── finishing ──

    def _finish(self, s: Session, now: float, reason: str) -> list[Action]:
        self.sessions.pop((s.room_id, s.owner), None)
        answer = "\n\n".join(s.parts).strip()
        if not answer:
            return [Post(s.room_id, f"⏱️ Time is up on {s.kind.label} ({s.prompt_id}), {s.owner_name}, "
                                    f"and I received no answer, so there is nothing to mark. Try again any time.")]

        self.state["counter"] = int(self.state.get("counter", 0)) + 1
        number = self.state["counter"]
        doc_name = f"sub-{number:04d}-{s.prompt_id}"
        words = count_words(answer)
        used = min(now, s.ends) - s.starts
        timing = f"{clock(used)} of {clock(s.total)}"
        if reason == "time":
            timing += " (collected when time ran out)"
        length = ""
        if s.kind.min_words:
            ok = words >= s.kind.min_words
            length = f"{words} words (minimum {s.kind.min_words} {'✓' if ok else '✗ UNDER LENGTH'})"
        else:
            length = f"{words} words"

        content = (
            f"BandUp submission {doc_name}\n"
            f"Date: {self._today()}\n"
            f"Who: {s.owner_name}\n"
            f"Task: {s.kind.label} ({s.kind.code})\n"
            f"Prompt {s.prompt_id}: {s.prompt}\n"
            f"Time used: {timing}\n"
            f"Length: {length}\n"
            f"\n--- ANSWER ---\n{answer}\n"
        )
        marker = self.config.examiner if s.kind.marker == "examiner" else self.config.speaking
        if s.kind.code == "S2":
            ask = "Please run two or three Part 3 follow-ups on this theme, one at a time, then mark it."
        else:
            ask = "Please mark it."
        handoff = (
            f"@{marker} {s.owner_name} finished {s.kind.label} {s.prompt_id}. "
            f"The answer is filed as room document {doc_name} (id {{doc_id}}). "
            f"Time {timing}; {length}. {ask}"
        )
        fallback = (
            f"@{marker} {s.owner_name} finished {s.kind.label} {s.prompt_id}, but I could not file it as a "
            f"room document, so here it is inline. Time {timing}; {length}. Prompt: {s.prompt}\n\n"
            f"ANSWER:\n{answer}"
        )
        actions: list[Action] = [
            Post(s.room_id, f"✅ Got it, {s.owner_name}: {length}, {timing}. Handing it to @{marker}."
                 if reason == "done" else
                 f"⏱️ Time is up, {s.owner_name}. I collected {length}. Handing it to @{marker}."),
            FileAnswer(
                room_id=s.room_id, name=doc_name,
                description=f"{s.owner_name}'s {s.kind.label} answer to {s.prompt_id}",
                instructions=(f"The answer to mark, filed by the proctor. Mark it as {s.kind.label}; "
                              f"the header gives the prompt, time used and word count."),
                content=content, handoff=handoff, fallback=fallback,
            ),
        ]
        if s.then:
            nxt, rest = s.then[0], s.then[1:]
            fake = Inbound(s.room_id, s.owner, s.owner_name, "", True)
            actions += self._start(fake, nxt, now, minutes=None, then=rest)
        return actions
