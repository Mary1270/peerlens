"""Adversarial regression tests of the kind a protocol steward would write:
each one names a way to extract money the work did not earn, or to strand
money the work did earn, and shows the contract does neither."""
import json
import unittest

from _bootstrap import (
    m, gl, CLOCK, REPO, REWARD, BOND, PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, JUROR_ADDRESSES,
    make_contract, set_caller, locked_job, get, pending, pass_deadline, adjudicate, register_jurors,
    review_json, other_review_json, late_review_json, dismissed_review_json, short_review_json, pages_of,
    reviews_url, MODEL_DELIVERED, MODEL_UNDETERMINED,
)
from test_jury import jury_case, run_vote, APPEAL_BOND

D, P, N, U = m.OUT_DELIVERED, m.OUT_PARTIAL, m.OUT_NOT_DELIVERED, m.OUT_UNDETERMINED


class TestNoUnearnedPayment(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def rule(self, pages, model=None):
        bid = locked_job(self.c, urls=list(pages), both=True)
        pass_deadline(self.c, bid)
        return bid, adjudicate(self.c, bid, pages, model=model)

    def test_padding_with_spaces_does_not_buy_a_delivery(self):
        _bid, job = self.rule(pages_of(review_json(body="LGTM" + " " * 5000)))
        self.assertEqual(job["tier1_outcome"], U)

    def test_padding_with_filler_words_passes_code_but_not_the_merit_check(self):
        _bid, job = self.rule(pages_of(review_json(body="nice " * 100)), MODEL_UNDETERMINED)
        self.assertEqual(job["tier1_outcome"], U)

    def test_a_review_by_the_sponsors_friend_is_not_the_reviewers(self):
        bid = locked_job(self.c, urls=[reviews_url()], both=True)
        pass_deadline(self.c, bid)
        CLOCK.advance(m.ABSENCE_LAG_SECONDS)
        job = adjudicate(self.c, bid, pages_of(other_review_json(2001)), model={"outcome": D, "reasoning": "x"})
        self.assertEqual(job["tier1_outcome"], U)
        self.assertEqual(job["tier1_assessment"]["delivered_reviews"], [])

    def test_a_review_posted_before_the_job_existed_cannot_be_recycled(self):
        old = review_json(1001, submitted_at="2026-02-01T00:00:00Z")
        bid = locked_job(self.c, urls=[reviews_url()], both=True)
        pass_deadline(self.c, bid)
        CLOCK.advance(m.ABSENCE_LAG_SECONDS)
        job = adjudicate(self.c, bid, pages_of(old), model={"outcome": N, "reasoning": "none in window"})
        self.assertEqual(job["tier1_outcome"], N)

    def test_a_review_of_another_pull_request_is_not_this_jobs_review(self):
        wrong = review_json(1001, pull_request_url="https://api.github.com/repos/acme/widgets/pulls/8")
        bid = locked_job(self.c, urls=[reviews_url()], both=True)
        pass_deadline(self.c, bid)
        CLOCK.advance(m.ABSENCE_LAG_SECONDS)
        job = adjudicate(self.c, bid, pages_of(wrong), model={"outcome": N, "reasoning": "x"})
        self.assertEqual(job["tier1_assessment"]["delivered_reviews"], [])
        self.assertEqual(job["tier1_outcome"], N)

    def test_a_late_review_never_pays_the_reviewer(self):
        bid, job = self.rule(pages_of(late_review_json()), {"outcome": D, "reasoning": "looks fine"})
        self.assertEqual(job["tier1_outcome"], U)
        self.assertEqual(job["tier1_assessment"]["delivered_reviews"], [])

    def test_the_model_saying_delivered_for_a_short_review_is_never_even_asked(self):
        prompts = []
        bid = locked_job(self.c, urls=[reviews_url()], both=True)
        pass_deadline(self.c, bid)
        job = adjudicate(self.c, bid, pages_of(short_review_json()), model=None, prompts=prompts)
        self.assertEqual((job["tier1_outcome"], prompts), (U, []))

    def test_an_undetermined_job_refunds_each_side_exactly(self):
        bid, _job = self.rule(pages_of(short_review_json()))
        from _bootstrap import pass_appeal_window
        pass_appeal_window(self.c, bid)
        set_caller(STRANGER_ADDRESS)
        self.c.settle_unappealed(bid)
        self.assertEqual((pending(self.c, PARTY_A_ADDRESS), pending(self.c, PARTY_B_ADDRESS)), (REWARD, BOND))


class TestNoStrandedPayment(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_a_dismissed_review_is_not_a_win_for_the_sponsor(self):
        bid = locked_job(self.c, urls=[reviews_url()], both=True)
        pass_deadline(self.c, bid)
        job = adjudicate(self.c, bid, pages_of(dismissed_review_json()), model=None)
        self.assertEqual(job["tier1_outcome"], U)
        self.assertEqual(json.loads(self.c.get_jury_options(bid)), sorted([N, P, U]))

    def test_a_proven_review_cannot_be_overturned_to_breach_by_a_jury(self):
        register_jurors(self.c, 6)
        bid = jury_case(self.c, tier1=D, pages=pages_of(review_json()), model=MODEL_DELIVERED)
        b = run_vote(self.c, bid, [N] * 5)
        self.assertFalse(b["jury"]["verification_passed"])
        self.assertEqual(b["final_outcome"], D)

    def test_a_jury_cannot_deliver_what_the_evidence_does_not_show(self):
        register_jurors(self.c, 6)
        bid = jury_case(self.c, tier1=U, pages=pages_of(short_review_json()), model=None)
        b = run_vote(self.c, bid, [D] * 5)
        self.assertFalse(b["jury"]["verification_passed"])
        self.assertEqual(b["final_outcome"], U)
        self.assertEqual(self.c.get_review_claim(REPO, 7, 1001), "")

    def test_a_jury_can_still_award_a_partial_split_for_a_too_short_review(self):
        register_jurors(self.c, 6)
        bid = jury_case(self.c, tier1=U, pages=pages_of(short_review_json()), model=None)
        b = run_vote(self.c, bid, [P] * 5)
        self.assertEqual(b["final_outcome"], P)
        self.assertEqual(pending(self.c, PARTY_B_ADDRESS) >= BOND + REWARD // 2, True)

    def test_stored_evidence_is_what_the_jury_is_bound_by_not_the_live_page(self):
        register_jurors(self.c, 6)
        bid = jury_case(self.c, tier1=D, pages=pages_of(review_json()), model=MODEL_DELIVERED)
        job = get(self.c, bid)
        self.assertTrue(m._frozen_intact(job["evidence_content"], job["evidence_content_root"]))
        self.assertEqual(job["tier1_assessment"]["delivered_reviews"], [1001])


class TestOwnershipAndRoles(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_only_the_named_reviewer_wallet_can_accept(self):
        from _bootstrap import create_job, accept_job, PaymentRejected
        bid = create_job(self.c)
        with self.assertRaises(PaymentRejected):
            accept_job(self.c, bid, party_b=STRANGER_ADDRESS)

    def test_the_sponsor_cannot_review_their_own_job(self):
        from _bootstrap import create_job, PaymentRejected
        with self.assertRaises(PaymentRejected):
            create_job(self.c, party_b=PARTY_A_ADDRESS)


if __name__ == "__main__":
    unittest.main()
