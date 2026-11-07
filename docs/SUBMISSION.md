# Submission kit

Deadline: **November 7, 11:59 PM IST** (November 8, 12:29 AM in Dhaka).

## Checklist

- [ ] Repo pushed to GitHub, **public**, with this README at the root
- [ ] Demo video recorded (≈3 min, see `DEMO_SCRIPT.md`), uploaded, link pasted at the top of the README
- [ ] Ran it for real once: template imported, proctor registered, one Task 2 marked and logged
- [ ] Screenshots in `assets/` (a marked essay, the hand-off, the weekly review) and linked from the README
- [ ] Track: **Track 02, Best Personal or Creative Workflow**
- [ ] Optional side tracks: **Best Blog** (post below), **Code with Wispr** (add your Wispr Insights screenshot), **#HowISwitch** post
- [ ] Starred the Switch repo (keyboard draw)
- [ ] You can explain every file: the FAQ says AI tools are fine if you understand what you submit

## Form answers

**Project name:** BandUp

**One line:** An IELTS study crew of AI agents, examiner, speaking partner, coach and a timed proctor, working with you and your study buddies in Discord or Telegram through Switch.

**Description (short):**
BandUp turns a group chat into an IELTS study room. A proctor agent I wrote from scratch against Switch's agent protocol times each task, collects the answer across messages, counts the words, files it as a Switch room document and hands it to the examiner (a Claude Code agent) by name. The examiner marks all four criteria with quotes and a rewrite, then hands a LOG line to the coach, who keeps a progress log and a study plan and runs weekly reviews from a linked HQ room. The whole crew, three agents, two linked rooms, documents, aliases and kickoffs, is one Switch template.

**How Switch is used:**
Rooms bridged to Discord/Telegram; a non-LLM protocol agent alongside Claude Code agents; agent-to-agent hand-offs by @alias; room documents for submissions, the marking guide, the progress log and the study plan; room instructions as each agent's briefing; a group with links so the coach can read the practice room from HQ; addressing policies so study buddies can use the markers; one template that creates it all. Without Switch there is no shared room, no clock agent, no hand-off and no record.

**Tech:** Switch (Console, templates, agent protocol), Claude Code (or Codex/OpenCode), Python 3 standard library, Discord/Telegram.

**What I'd build next:** voice-note transcription for Speaking via attachments; a shared leaderboard room for a study group; Reading and Listening drills with auto-marking by the proctor.

## Blog post draft (Best Blog side track)

Publish on Hashnode, dev.to or Medium. Add your own screenshots where marked.

---

### I gave my IELTS prep a team: building BandUp with Switch

I'm preparing for IELTS and, like most people, I'm doing it alone. I can find endless practice questions. What I can't find is someone to mark my essays every day, a clock that keeps me honest, and a record that tells me whether I'm actually improving.

For the Flip the Switch hackathon I built that team, and put it where I already spend my day: a group chat.

**What Switch is, in one paragraph.** Switch connects AI agents to team chat. Every channel becomes a *room*; people and agents are members of it; agents get the room's instructions, documents and links when they join. It doesn't ship agents or models. You bring Claude Code, Codex, OpenCode, or anything that speaks its protocol.

**The crew.** Four agents in two rooms:

- `@proctor` sets timed tasks and collects answers. I wrote it from scratch in Python with no LLM.
- `@examiner` marks Writing on the four IELTS criteria. A Claude Code agent.
- `@speaking` runs Speaking interviews and marks them.
- `@coach` keeps the progress log and my study plan.

*[screenshot: the practice room after one task]*

**Step 1: one template for everything.** A Switch template is a YAML file that describes agents and rooms. Mine creates three agents, a group with two rooms (`practice` and `hq`), links between them, room aliases so `bandup-examiner` answers to `@examiner`, a read-only marking guide, and kickoff messages that wake the agents up. Switch Console turns the `params:` block into a form, so a friend can set up their own crew by changing four fields. Switch's own linter checks it in CI.

**Step 2: a protocol agent with no LLM.** The proctor taught me the most. The protocol is small: register once with a registration key, open a server-sent event stream for a room with a connection id you generate and keep, beat every two seconds, and call operations over HTTP. A stopwatch doesn't need a model, and Switch doesn't care: in the room it's a teammate like the others. *[snippet: the stream URL and heartbeat]*

**Step 3: hand-offs through room documents.** Discord caps messages at 2,000 characters, and a Task 2 essay is often longer. So I answer in several `ESSAY:` messages; the proctor stitches them, counts words, and files the result as a room document. Then it posts `@examiner … room document sub-0007-W2-03`. The examiner loads the document, not a chat excerpt. Every submission becomes a permanent record anyone in the room can open.

**Step 4: memory between rooms, on purpose.** Switch keeps context inside a room, and I liked that: my HQ chats with the coach shouldn't clutter the practice floor. When I ask for a weekly review, the coach follows the link from HQ to the practice room, reads `progress-log`, and comes back to write the plan. Context crosses only where I linked it.

**What I learned**

- Write room instructions like onboarding a new teammate: who does what, the exact hand-off format, and "fail loud".
- An agent can only edit documents it created, so have the agent create the documents it needs to maintain. The kickoff is a good place for that.
- Tell agents about your chat app: Discord and Telegram don't render tables the same way, so mine use short bullets.
- Not every agent needs a model. The most reliable member of my crew is a stopwatch.

**Try it:** `<repo link>`. Import the lite template, which needs no proctor, and paste an essay to `@examiner`.

---

## #HowISwitch posts

**X / Twitter**
> Preparing for IELTS alone = no marker, no clock, no memory. So I built BandUp with Switch for #FlipTheSwitch: an examiner, a speaking partner, a coach and a proctor bot I wrote from scratch, all in my Discord, handing work to each other. #HowISwitch <repo> <video>

**LinkedIn**
> For the Flip the Switch hackathon (WeMakeDevs × DevTools Academy × Switch) I built BandUp: an IELTS study crew that lives in a group chat.
>
> • A proctor agent (plain Python, Switch's agent protocol) times each task and files answers as room documents
> • A Claude Code examiner marks all four Writing criteria with quotes from my essay and a rewrite
> • A coach logs every score and builds my weekly plan from a linked room
>
> One Switch template creates the whole crew. Repo and demo: <links> #HowISwitch
