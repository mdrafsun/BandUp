import unittest

from bandup_proctor.engine import Config, FileAnswer, Inbound, Post, PromptBank, Proctor, count_words

ROOM = "room-1"


def msg(body, sender="u-omar", name="Omar", addressed=None):
    if addressed is None:
        addressed = "@proctor" in body.lower()
    return Inbound(ROOM, sender, name, body, addressed)


def essay(words):
    return " ".join(["word"] * words)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.p = Proctor(Config(grace_seconds=30), bank=PromptBank.load(seed=7), today=lambda: "2026-11-06")
        self.t = 1000.0

    def posts(self, actions):
        return [a.body for a in actions if isinstance(a, Post)]

    def files(self, actions):
        return [a for a in actions if isinstance(a, FileAnswer)]

    def test_count_words(self):
        self.assertEqual(count_words("It's a well-known fact: 3 cars, 2 bikes."), 8)

    def test_help(self):
        out = self.p.handle(msg("@proctor help"), self.t)
        self.assertIn("writing task 2", out[0].body)

    def test_writing_task_two_multi_part_then_done(self):
        out = self.p.handle(msg("@proctor writing task 2"), self.t)
        self.assertIn("Writing Task 2", out[0].body)
        self.assertIn("40:00", out[0].body)
        self.assertEqual(self.p.handle(msg("hang on, getting coffee"), self.t + 5), [])  # chatter
        self.p.handle(msg("ESSAY: " + essay(150)), self.t + 600)
        out = self.p.handle(msg(essay(120) + "\nDONE"), self.t + 2100)
        filed = self.files(out)
        self.assertEqual(len(filed), 1)
        doc = filed[0]
        self.assertTrue(doc.name.startswith("sub-0001-W2-"))
        self.assertIn("270 words (minimum 250 ✓)", doc.content)
        self.assertIn("35:00 of 40:00", doc.content)
        self.assertTrue(doc.handoff.startswith("@examiner "))
        self.assertIn("{doc_id}", doc.handoff)
        self.assertIn("ANSWER", doc.fallback)
        self.assertNotIn((ROOM, "u-omar"), self.p.sessions)

    def test_under_length_is_flagged(self):
        self.p.handle(msg("@proctor task 1"), self.t)
        out = self.p.handle(msg("ESSAY: " + essay(90) + " DONE"), self.t + 60)
        self.assertIn("UNDER LENGTH", self.files(out)[0].content)

    def test_done_by_addressing_the_proctor(self):
        self.p.handle(msg("@proctor w2"), self.t)
        self.p.handle(msg("essay - " + essay(260)), self.t + 60)
        out = self.p.handle(msg("@proctor done"), self.t + 120)
        self.assertEqual(len(self.files(out)), 1)

    def test_time_up_collects_what_was_written(self):
        self.p.handle(msg("@proctor writing task 2 10 min"), self.t)
        self.p.handle(msg("ESSAY: " + essay(200)), self.t + 60)
        self.assertEqual(self.files(self.p.tick(self.t + 600)), [])  # still inside the grace period
        out = self.p.tick(self.t + 631)
        filed = self.files(out)
        self.assertEqual(len(filed), 1)
        self.assertIn("collected when time ran out", filed[0].content)

    def test_warning_once_before_time_is_up(self):
        self.p.handle(msg("@proctor writing task 2"), self.t)
        self.assertEqual(self.p.tick(self.t + 60), [])
        warn = self.posts(self.p.tick(self.t + 30 * 60))
        self.assertEqual(len(warn), 1)
        self.assertIn("10:00 left", warn[0])
        self.assertEqual(self.p.tick(self.t + 31 * 60), [])

    def test_time_up_with_no_answer(self):
        self.p.handle(msg("@proctor writing task 2 1 min"), self.t)
        out = self.p.tick(self.t + 100)
        self.assertEqual(self.files(out), [])
        self.assertIn("no answer", self.posts(out)[0])

    def test_speaking_part_two_prep_then_handoff_to_speaking(self):
        out = self.p.handle(msg("@proctor speaking part 2"), self.t)
        self.assertIn("1:00 to prepare", out[0].body)
        self.assertEqual(self.posts(self.p.tick(self.t + 30)), [])
        self.assertIn("start speaking now", self.posts(self.p.tick(self.t + 61))[0])
        out = self.p.handle(msg("ANSWER: " + essay(180) + "\ndone"), self.t + 170)
        doc = self.files(out)[0]
        self.assertIn("-S2-", doc.name)
        self.assertTrue(doc.handoff.startswith("@speaking "))
        self.assertIn("Part 3", doc.handoff)

    def test_parts_one_and_three_go_to_the_speaking_partner(self):
        self.assertTrue(self.p.handle(msg("@proctor speaking part 1"), self.t)[0].body.startswith("@speaking"))
        self.assertTrue(self.p.handle(msg("@proctor s3"), self.t)[0].body.startswith("@speaking"))
        self.assertEqual(self.p.sessions, {})

    def test_mock_runs_task_two_after_task_one(self):
        self.p.handle(msg("@proctor mock writing"), self.t)
        out = self.p.handle(msg("ESSAY: " + essay(160) + "\nDONE"), self.t + 1100)
        self.assertEqual(len(self.files(out)), 1)
        self.assertIn("Writing Task 2", self.posts(out)[-1])
        self.assertEqual(self.p.sessions[(ROOM, "u-omar")].kind.code, "W2")

    def test_named_prompt_and_unknown_prompt(self):
        out = self.p.handle(msg("@proctor writing task 2 W2-04"), self.t)
        self.assertIn("W2-04", out[0].body)
        self.p.handle(msg("@proctor cancel"), self.t)
        out = self.p.handle(msg("@proctor W2-99"), self.t)
        self.assertEqual(self.p.sessions, {})
        self.assertIn("no prompt", out[0].body)

    def test_lowercase_done_inside_the_essay_does_not_submit(self):
        self.p.handle(msg("@proctor task 2"), self.t)
        out = self.p.handle(msg("ESSAY: In the end the work gets done."), self.t + 60)
        self.assertEqual(out, [])
        self.assertIn((ROOM, "u-omar"), self.p.sessions)

    def test_general_training_task_one(self):
        out = self.p.handle(msg("@proctor task 1 gt"), self.t)
        self.assertIn("W1G-", out[0].body)

    def test_one_task_at_a_time_but_buddies_are_independent(self):
        self.p.handle(msg("@proctor task 2"), self.t)
        out = self.p.handle(msg("@proctor task 1"), self.t + 1)
        self.assertIn("already have", out[0].body)
        self.p.handle(msg("@proctor task 2", sender="u-sara", name="Sara"), self.t + 2)
        self.assertEqual(len(self.p.sessions), 2)
        # Sara's essay does not end Omar's task
        self.p.handle(msg("ESSAY: " + essay(255) + " DONE", sender="u-sara", name="Sara"), self.t + 10)
        self.assertIn((ROOM, "u-omar"), self.p.sessions)

    def test_prompts_are_not_repeated_for_the_same_person(self):
        seen = set()
        for i in range(len(self.p.bank.ids("W2"))):
            out = self.p.handle(msg("@proctor task 2"), self.t + i)
            pid = out[0].body.split("prompt ")[1].split()[0]
            self.assertNotIn(pid, seen)
            seen.add(pid)
            self.p.handle(msg("@proctor cancel"), self.t + i)

    def test_submission_counter_persists_in_state(self):
        state = {"counter": 41, "seen": {}}
        p = Proctor(Config(), bank=PromptBank.load(seed=1), state=state)
        p.handle(msg("@proctor task 2"), self.t)
        out = p.handle(msg("ESSAY: " + essay(260) + " DONE"), self.t + 100)
        self.assertTrue(self.files(out)[0].name.startswith("sub-0042-"))
        self.assertEqual(state["counter"], 42)

    def test_time_scale_for_demos(self):
        p = Proctor(Config(time_scale=0.05), bank=PromptBank.load(seed=1))
        out = p.handle(msg("@proctor task 2"), self.t)
        self.assertIn("2:00 starts now", out[0].body)


if __name__ == "__main__":
    unittest.main()
