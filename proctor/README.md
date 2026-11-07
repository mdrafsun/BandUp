# BandUp proctor

A Switch agent written from scratch against the [agent protocol](https://docs.switchagents.ai/flintai/switch/internals/agent-protocol): server-sent events in, HTTP out. Python standard library only, no LLM, nothing to install.

It is the exam room clock. It sets a timed IELTS task, collects the answer however many messages it takes, counts the words, files the answer as a **Switch room document**, and hands it to the right marker by name. The markers are Claude Code agents; the proctor is not, and Switch lets both sit in the same room as teammates.

## What it does in the room

| You send | The proctor |
|---|---|
| `@proctor writing task 2` | Posts a prompt, starts 40:00, warns at 10:00 left |
| `@proctor task 1` / `task 1 gt` | Task 1, Academic or General Training, 20:00 |
| `@proctor speaking part 2` | Cue card, 1:00 prep, then 2:00 to answer |
| `@proctor speaking part 1` / `part 3` | Hands the live interview to `@speaking` |
| `@proctor mock writing` | Task 1 then Task 2, back to back |
| `@proctor task 2 W2-04 25 min` | A chosen prompt, a chosen time |
| `@proctor prompts` · `status` · `cancel` | What it says |
| `ESSAY: ...` (or `ANSWER: ...`) then `DONE` | Collects the answer, across as many messages as you need |

When you send `DONE`, or the clock runs out (30 seconds' grace), it:

1. creates the room document `sub-0007-W2-03` with the prompt, time used, word count and your answer;
2. posts `@examiner Omar finished Writing Task 2 W2-03. The answer is filed as room document sub-0007-W2-03 (id …). Time 37:12 of 40:00; 263 words (minimum 250 ✓). Please mark it.`

If the document cannot be created it says so and posts the answer inline instead, so nothing is lost quietly. Each person in the room gets their own clock, and nobody is given the same prompt twice.

## Set it up

You need Python 3.10+ and Switch Console with a server running.

```bash
cd proctor

# 1. A registration key: Switch Console → Settings → API keys → Registration keys → Create.
# 2. The server URL: the one Switch Console shows for your server (http://localhost:<port>).
#    Check it: curl http://localhost:<port>/health
python -m bandup_proctor register --url http://localhost:<port> --key <registration key>

# 3. Run it. Leave this terminal open.
python -m bandup_proctor run
```

`register` writes `SWITCH_URL`, `PROCTOR_AGENT_ID` and `PROCTOR_API_KEY` to `.env` (mode 600). The API key is shown once.

A registered agent starts closed: it answers you and your agents. To let study buddies use it too, open the agent in Switch Console and widen who can address it to the practice room.

Now run `templates/bandup.template.yaml`: its `proctor` input finds the agent by name and puts it in the practice room. The runner notices new rooms within a minute.

### Recording a demo

`PROCTOR_TIME_SCALE=0.05 python -m bandup_proctor run` makes a 40 minute task last 2 minutes and a Part 2 cue card 6 seconds of prep. Or ask for a time: `@proctor task 2 3 min`.

### Try it without Switch

```bash
python -m bandup_proctor simulate --scale 0.02
omar: @proctor writing task 2
omar: ESSAY: Some people argue ...
omar: DONE
```

## How it speaks the protocol

- **Registration.** `POST /agents` with the registration key, as an `always_on` agent, since it is reachable whenever the process runs.
- **One connection per room.** A connection id is generated once per room, saved in `proctor-state.json` and reused on every reconnect, as the protocol asks. The room is declared on the stream URL (`scope=single&rooms=<id>`) so catch-up covers it from the first frame.
- **Heartbeat.** `POST /connection/beat` every 2 seconds with the `generation` from `connection_state`; a 404 or 409 reopens the stream rather than backing off.
- **Resume.** `Last-Event-ID` on reconnect; `gap` frames are logged, `evicted` closes and reopens.
- **Operations.** `list_rooms` to find its rooms, then `post_message` and `create_room_document` with the `X-Switch-Connection-Id` header, so they act in the room that connection holds. If the op route refuses, a post falls back to `POST /agents/{id}/message`.
- **Its own messages** coming back on the stream are recognised by event id and name and ignored.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

`test_engine.py` drives every timer with a fake clock. `test_runner_mock_switch.py` runs the real runner against a fake Switch bridge over HTTP and SSE: registration, the stream URL, the heartbeat's generation, op headers, a task from prompt to hand-off, and ignoring its own echo.

## Files

```
bandup_proctor/
  engine.py         the logic: tasks, clocks, answers, hand-offs (no network)
  switch_client.py  the protocol client (SSE + HTTP, stdlib)
  cli.py            register / run / simulate, the room workers and heartbeats
  prompts.json      practice prompts written for BandUp (not official IELTS material)
```
