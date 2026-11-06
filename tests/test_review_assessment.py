import datetime
import unittest

from _bootstrap import (
    m, anchors, assess, gh_ts, BASE_EPOCH, DEADLINE_EPOCH, DAY, MIN_CHARS,
    review_json, other_review_json, late_review_json, dismissed_review_json, short_review_json,
    pages_of, reviews_url,
)


def run(*reviews, b=None, **kw):
    return assess(pages_of(*reviews), b or anchors(**kw))


class TestVerdicts(unittest.TestCase):
    def test_a_substantive_in_window_review_is_delivered(self):
        a = run(review_json(1001))
        self.assertEqual(a["delivered_reviews"], [1001])
        self.assertFalse(a["non_delivery_supported"])
        self.assertEqual(a["reports"][0]["verdict"], "delivered")

    def test_every_substantive_state_delivers(self):
        for state in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
            with self.subTest(state=state):
                self.assertEqual(run(review_json(1001, state=state))["delivered_reviews"], [1001])

    def test_unknown_states_are_ambiguous_not_delivered(self):
        for state in ("", "WEIRD", "MERGED"):
            with self.subTest(state=state):
                a = run(review_json(1001, state=state))
                self.assertEqual((a["delivered_reviews"], a["ambiguous_reviews"]), ([], [1001]))

    def test_a_pending_review_is_unrelated(self):
        a = run(review_json(1001, state="PENDING", submitted_at=None))
        self.assertEqual(a["reports"][0]["verdict"], "unrelated")
        self.assertTrue(a["absence_proven"])

    def test_a_dismissed_review_is_rejected_never_failed(self):
        a = run(dismissed_review_json(1001))
        self.assertEqual(a["rejected_reviews"], [1001])
        self.assertFalse(a["non_delivery_supported"])

    def test_a_short_review_is_ambiguous(self):
        a = run(short_review_json(1001))
        self.assertEqual(a["ambiguous_reviews"], [1001])
        self.assertEqual(a["delivered_reviews"], [])

    def test_the_length_threshold_is_inclusive(self):
        ok = run(review_json(1001, body="x" * MIN_CHARS))
        short = run(review_json(1001, body="x" * (MIN_CHARS - 1)))
        self.assertEqual(ok["delivered_reviews"], [1001])
        self.assertEqual(short["ambiguous_reviews"], [1001])

    def test_the_anchored_threshold_is_what_counts(self):
        body = "y" * 30
        self.assertEqual(run(review_json(1001, body=body), min_review_chars=30)["delivered_reviews"], [1001])
        self.assertEqual(run(review_json(1001, body=body), min_review_chars=31)["ambiguous_reviews"], [1001])

    def test_whitespace_padding_does_not_count_toward_the_threshold(self):
        a = run(review_json(1001, body="LGTM" + " " * 1000))
        self.assertEqual(a["ambiguous_reviews"], [1001])

    def test_a_review_before_the_job_existed_is_unrelated(self):
        a = run(review_json(1001, submitted_at=gh_ts(BASE_EPOCH - 1)))
        self.assertEqual(a["delivered_reviews"], [])
        self.assertTrue(a["absence_proven"])

    def test_the_window_boundaries_are_inclusive(self):
        self.assertEqual(run(review_json(1001, submitted_at=gh_ts(BASE_EPOCH)))["delivered_reviews"], [1001])
        self.assertEqual(run(review_json(1001, submitted_at=gh_ts(DEADLINE_EPOCH)))["delivered_reviews"], [1001])
        a = run(review_json(1001, submitted_at=gh_ts(DEADLINE_EPOCH + 1)))
        self.assertEqual((a["delivered_reviews"], a["failed_reviews"]), ([], [1001]))

    def test_an_unreadable_timestamp_is_never_delivery(self):
        a = run(review_json(1001, submitted_at="soon"))
        self.assertEqual(a["delivered_reviews"], [])
        self.assertEqual(a["reports"][0]["verdict"], "unrelated")

    def test_someone_elses_review_never_delivers(self):
        a = run(other_review_json(2001))
        self.assertEqual(a["delivered_reviews"], [])
        self.assertTrue(a["absence_proven"])

    def test_one_good_review_among_bad_ones_delivers(self):
        a = run(short_review_json(1001), review_json(1002), dismissed_review_json(1003), late_review_json(1004))
        self.assertEqual(a["delivered_reviews"], [1002])
        self.assertEqual((a["ambiguous_reviews"], a["rejected_reviews"], a["failed_reviews"]), ([1001], [1003], [1004]))
        self.assertFalse(a["non_delivery_supported"])


class TestAbsenceAndNonDelivery(unittest.TestCase):
    def test_an_empty_complete_list_proves_absence(self):
        a = run()
        self.assertTrue(a["absence_proven"])
        self.assertEqual(a["unreadable"], 0)

    def test_absence_only_counts_after_the_settling_lag(self):
        early = run(b=anchors(absence_ready=False))
        late = run(b=anchors(absence_ready=True))
        self.assertFalse(early["non_delivery_supported"])
        self.assertTrue(early["absence_proven"] and not early["absence_counted"])
        self.assertTrue(late["non_delivery_supported"] and late["absence_counted"])

    def test_a_late_review_supports_non_delivery_without_waiting(self):
        a = run(late_review_json(1001), b=anchors(absence_ready=False))
        self.assertTrue(a["non_delivery_supported"])

    def test_a_truncated_list_cannot_prove_absence(self):
        page = [other_review_json(5000 + i) for i in range(100)]
        a = assess({reviews_url(): page}, anchors())
        self.assertFalse(a["absence_proven"])
        self.assertEqual(a["unreadable"], 1)
        self.assertFalse(a["non_delivery_supported"])

    def test_a_truncated_list_still_shows_a_review_that_is_present(self):
        page = [other_review_json(5000 + i) for i in range(99)] + [review_json(1001)]
        a = assess({reviews_url(): page}, anchors())
        self.assertEqual(a["delivered_reviews"], [1001])

    def test_an_unparseable_page_is_unreadable(self):
        a = assess({reviews_url(): "rate limited"}, anchors())
        self.assertEqual((a["unreadable"], a["parsed_lists"], a["absence_proven"]), (1, 0, False))

    def test_a_missing_page_is_unreadable_not_absence(self):
        import _bootstrap as bs
        b = anchors()
        with bs._patch.object(bs.gl.nondet.web, "render", side_effect=RuntimeError("404")):
            pages = m._gather_pages([reviews_url()], b)
        a = m._assess(pages, b)
        self.assertEqual((a["unreadable"], a["absence_proven"]), (1, False))

    def test_nothing_supports_non_delivery_when_anything_else_was_found(self):
        for extra in (review_json(1001), short_review_json(1001), dismissed_review_json(1001)):
            with self.subTest(extra=extra["state"]):
                a = run(late_review_json(1002), extra, b=anchors(absence_ready=True))
                self.assertFalse(a["non_delivery_supported"])


class TestNeedsRetryAndModelNeeded(unittest.TestCase):
    def test_retry_only_when_nothing_decisive_and_evidence_is_immature(self):
        self.assertTrue(m._needs_retry(assess({reviews_url(): "oops"}, anchors())))
        self.assertTrue(m._needs_retry(run(b=anchors(absence_ready=False))))
        self.assertFalse(m._needs_retry(run(b=anchors(absence_ready=True))))
        self.assertFalse(m._needs_retry(run(short_review_json(1001))))
        self.assertFalse(m._needs_retry(run(dismissed_review_json(1001))))

    def test_model_is_consulted_for_merit_and_for_non_delivery_only(self):
        d = run(review_json(1001))
        n = run(b=anchors(absence_ready=True))
        amb = run(short_review_json(1001))
        rej = run(dismissed_review_json(1001))
        self.assertTrue(m._model_needed(d, True))
        self.assertTrue(m._model_needed(d, False))
        self.assertFalse(m._model_needed(n, True))
        self.assertTrue(m._model_needed(n, False))
        self.assertFalse(m._model_needed(amb, False))
        self.assertFalse(m._model_needed(rej, False))
        self.assertFalse(m._model_needed(d, False, model_allowed=False))


class TestDecisionTable(unittest.TestCase):
    D, P, N, U = m.OUT_DELIVERED, m.OUT_PARTIAL, m.OUT_NOT_DELIVERED, m.OUT_UNDETERMINED

    def test_delivered_needs_an_affirmative_merit_answer(self):
        a = run(review_json(1001))
        self.assertEqual(m._decide(a, self.D), self.D)
        self.assertEqual(m._decide(a, self.P), self.P)
        for claim in (self.U, self.N, None):
            self.assertEqual(m._decide(a, claim), self.U)

    def test_not_delivered_needs_code_support_and_the_model(self):
        a = run(late_review_json(1001))
        self.assertEqual(m._decide(a, self.N), self.N)
        for claim in (self.D, self.P, self.U, None):
            self.assertEqual(m._decide(a, claim), self.U)

    def test_the_model_alone_can_never_deliver_or_condemn(self):
        ambiguous = run(short_review_json(1001))
        for claim in (self.D, self.P, self.N):
            self.assertEqual(m._decide(ambiguous, m._apply_backstop(claim, ambiguous)), self.U)

    def test_backstop_downgrades_delivery_without_a_proven_review(self):
        self.assertEqual(m._apply_backstop(self.D, run(short_review_json(1001))), self.U)
        self.assertEqual(m._apply_backstop(self.D, run(review_json(1001))), self.D)
        self.assertEqual(m._apply_backstop(self.P, run()), self.P)

    def test_premature_guard(self):
        for outcome in (self.N, self.P):
            self.assertEqual(m._premature_guard(outcome, True), self.U)
            self.assertEqual(m._premature_guard(outcome, False), outcome)
        self.assertEqual(m._premature_guard(self.D, True), self.D)


class TestJuryOptions(unittest.TestCase):
    D, P, N, U = m.OUT_DELIVERED, m.OUT_PARTIAL, m.OUT_NOT_DELIVERED, m.OUT_UNDETERMINED

    def opts(self, a, before=False):
        return m._jury_options(a, before)

    def test_options_follow_the_facts(self):
        self.assertEqual(self.opts(run(review_json(1001))), sorted([self.D, self.P, self.U]))
        self.assertEqual(self.opts(run(dismissed_review_json(1001))), sorted([self.N, self.P, self.U]))
        self.assertEqual(self.opts(run(short_review_json(1001))), sorted([self.P, self.U]))
        self.assertEqual(self.opts(run(late_review_json(1001))), sorted([self.N, self.U]))
        self.assertEqual(self.opts(run(b=anchors(absence_ready=False))), [self.U])

    def test_before_the_deadline_only_delivery_is_possible(self):
        self.assertEqual(self.opts(run(review_json(1001)), before=True), sorted([self.D, self.U]))
        self.assertEqual(self.opts(run(dismissed_review_json(1001)), before=True), [self.U])

    def test_delivery_is_never_offered_without_a_proven_review(self):
        for reviews in ((short_review_json(1001),), (dismissed_review_json(1001),), (late_review_json(1001),), ()):
            with self.subTest(n=len(reviews)):
                self.assertNotIn(self.D, self.opts(run(*reviews, b=anchors(absence_ready=True))))


if __name__ == "__main__":
    unittest.main()
