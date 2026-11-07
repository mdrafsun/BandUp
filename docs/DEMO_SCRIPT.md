# Demo video: 3 minutes, shot by shot

Judges score "Presentation: do the demo video and README make clear how Switch is used?" So every scene names the Switch feature on screen. Record at 1080p, edit out waiting, don't speed up footage.

## Before you record

- Run the proctor with a short clock: `PROCTOR_TIME_SCALE=0.05 python -m bandup_proctor run` (Task 2 = 2 minutes, Part 2 prep = 3 seconds), or ask for `task 2 2 min`.
- Have one finished ~260-word essay ready to paste in three messages (Discord's 2,000-character limit is part of the story).
- Run the template once beforehand so the agents' first sessions are warm, then **run it again live** under a new prefix (`demo`) for scene 2, or show the first run's result.
- Arrange screen: Discord on the left (70%), Switch Console on the right (30%). Terminal with the proctor's log in a corner.
- Optional: a second Discord account as a study buddy ("Sara").

## Script

| Time | On screen | Voice-over (say it, or put it on captions) |
|---|---|---|
| 0:00–0:12 | Title card: **BandUp: your IELTS study crew, in your group chat** over the Discord channel | "Preparing for IELTS alone means nobody marks you, nobody times you, and nobody remembers how you did last week. I fixed that with Switch." |
| 0:12–0:35 | Switch Console → Templates → Import `bandup.template.yaml` → the form (name, module, target 7.0, test date, Discord) → **Create**. The creates-rail shows 3 agents, 2 rooms, a group. | "One template. Switch creates three Claude Code agents, two linked rooms in Discord, the room documents, the aliases, and puts my own Python agent, the proctor, in with them." |
| 0:35–0:50 | Discord: `#demo-practice` kickoff. Coach creates `progress-log`, examiner and speaking say hello. Cut to `#demo-hq`: coach asks its three questions. | "The kickoff wakes the agents. The coach creates the documents it owns." |
| 0:50–1:20 | Type `@proctor writing task 2`. Prompt + timer. Paste the essay in 3 messages starting `ESSAY:`, then `DONE`. Proctor: "Got it, 263 words, 1:41 of 2:00". | "The proctor isn't an LLM. It's plain standard-library Python speaking Switch's agent protocol: events in, HTTP out. It keeps the clock, stitches my messages together and counts the words." |
| 1:20–1:35 | Proctor posts the hand-off `@examiner … room document sub-0001-W2-03 (id …)`. Show `!list-documents` in the channel, or the room's documents in Switch Console. | "It files my answer as a Switch room document and hands it to the examiner by name. Agent to agent, in the channel, where I can see it." |
| 1:35–2:05 | Examiner's marking: band estimate, four criteria with my quoted sentences, the rewrite, drills, and the `@coach LOG …` line. Coach replies "Logged. W2: 6.5 (target 7.0)". | "The examiner loads the document and the room's marking guide, marks all four criteria with my own sentences as evidence, and logs the score with the coach." |
| 2:05–2:25 | `@proctor speaking part 2` → cue card → answer dictated with Wispr Flow (`ANSWER: …`, `DONE`) → @speaking asks a Part 3 follow-up → marks it. | "Speaking works the same way. I dictate my answer, and the speaking partner asks Part 3 follow-ups before marking." |
| 2:25–2:45 | `#demo-hq`: `@coach weekly review`. Coach: this week vs last, biggest weakness, next week's plan; `study-plan` updated. | "In HQ, the coach follows the room link to the practice floor, reads the progress log, and plans my week. Context moves between rooms only where I linked them." |
| 2:45–3:00 | README diagram + the table "Why this needs Switch". End card: repo URL, #HowISwitch. | "Rooms, any agent through the protocol, agent hand-offs, room documents, links and a one-file template. Without Switch, it's one chatbot tab. With it, it's a study team. BandUp, link below." |

## Tips

- Show the proctor's terminal log for two seconds during scene 4: `watching room demo-practice`, `connected (cursor …)`. It proves the protocol client is real.
- If a marking reply is long, scroll it at reading speed once, then cut.
- Keep captions on: judges often watch muted.
- Upload to YouTube as **Unlisted** or Public, and paste the link at the top of the README.
