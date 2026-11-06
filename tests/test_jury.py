import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    late_review_json, pages_of, review_json, short_review_json, reviews_url,
    m, gl, CLOCK, DAY, PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, JUROR_ADDRESSES, REPO, REWARD, BOND,
    make_contract, set_caller, call_payable, ruled_job, get, pending, pass_appeal_window, draw_jury,
    commit_hash, register_jurors, expect_user_error, active_job,
)

D, P, N, U = m.OUT_DELIVERED, m.OUT_PARTIAL, m.OUT_NOT_DELIVERED, m.OUT_UNDETERMINED
CLOSED_PAGES = pages_of(late_review_json())  # submitted after the deadline: supports NOT_DELIVERED
APPEAL_BOND = REWARD * 2000 // 10000
JURY_FEE = APPEAL_BOND * m.PeerLens.JURY_FEE_BPS // 10000


def juror_key(i):
    return JUROR_ADDRESSES[i].lower()


def appealed(c, tier1=N, model=None, jurors=6, appellant=PARTY_B_ADDRESS, pages=None):
    """A ruled job with a filed appeal. Jurors register before the job
    is accepted, as eligibility requires."""
    register_jurors(c, jurors)
    bid = ruled_job(c, pages=pages or CLOSED_PAGES, model=model if model is not None else {"outcome": tier1})
    set_caller(appellant)
    call_payable(c, "file_appeal", APPEAL_BOND, bid)
    return bid


def jury_case(c, **kw):
    bid = appealed(c, **kw)
    draw_jury(c, bid)
    return bid


def selected(c, bid):
    return [a.lower() for a in get(c, bid)["jury"]["selected_jurors"]]


def vote(c, bid, juror, outcome, salt="s"):
    set_caller(juror)
    c.commit_vote(bid, commit_hash(outcome, salt, bid, juror))


def commit_all(c, bid, votes):
    """votes: list of outcomes assigned to the selected jurors in order."""
    for addr, outcome in zip(selected(c, bid), votes):
        if outcome is not None:
            vote(c, bid, addr, outcome)


def open_reveal(c, bid):
    CLOCK.set(datetime.datetime.fromisoformat(get(c, bid)["jury"]["commit_deadline"]) + datetime.timedelta(seconds=1))


def reveal_all(c, bid, votes):
    open_reveal(c, bid)
    for addr, outcome in zip(selected(c, bid), votes):
        if outcome is not None:
            set_caller(addr)
            c.reveal_vote(bid, outcome, "s")


def close_reveal(c, bid):
    CLOCK.set(datetime.datetime.fromisoformat(get(c, bid)["jury"]["reveal_deadline"]) + datetime.timedelta(seconds=1))


def settle_jury(c, bid, model=None):
    close_reveal(c, bid)
    set_caller(STRANGER_ADDRESS)
    answer = model if model is not None else {"outcome": N}
    with patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="json": answer):
        c.settle_jury(bid)
    return get(c, bid)


def run_vote(c, bid, votes, model=None):
    commit_all(c, bid, votes)
    reveal_all(c, bid, votes)
    return settle_jury(c, bid, model)


class TestFileAppeal(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_a_party_can_appeal_with_the_exact_bond(self):
        bid = appealed(self.c)
        b = get(self.c, bid)
        self.assertEqual(b["status"], "appeal_filed")
        self.assertEqual(b["appeal"]["appellant"].lower(), PARTY_B_ADDRESS)
        self.assertEqual(b["appeal"]["bond_amount"], str(APPEAL_BOND))

    def test_the_sponsor_can_appeal_too(self):
        bid = appealed(self.c, appellant=PARTY_A_ADDRESS)
        self.assertEqual(get(self.c, bid)["appeal"]["appellant"].lower(), PARTY_A_ADDRESS)

    def test_a_stranger_cannot_appeal(self):
        register_jurors(self.c, 6)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        set_caller(STRANGER_ADDRESS)
        expect_user_error(self, call_payable, self.c, "file_appeal", APPEAL_BOND, bid)

    def test_wrong_bond_amounts_are_rejected(self):
        register_jurors(self.c, 6)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        set_caller(PARTY_B_ADDRESS)
        for amount in (0, APPEAL_BOND - 1, APPEAL_BOND + 1):
            with self.subTest(amount=amount):
                expect_user_error(self, call_payable, self.c, "file_appeal", amount, bid)

    def test_too_few_jurors_blocks_the_appeal(self):
        register_jurors(self.c, 4)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        set_caller(PARTY_B_ADDRESS)
        self.assertIn("Not enough", expect_user_error(self, call_payable, self.c, "file_appeal", APPEAL_BOND, bid))
        self.assertEqual(get(self.c, bid)["status"], "ruled")

    def test_late_appeal_is_rejected(self):
        register_jurors(self.c, 6)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        pass_appeal_window(self.c, bid)
        set_caller(PARTY_B_ADDRESS)
        expect_user_error(self, call_payable, self.c, "file_appeal", APPEAL_BOND, bid)

    def test_second_appeal_is_rejected(self):
        bid = appealed(self.c)
        set_caller(PARTY_A_ADDRESS)
        expect_user_error(self, call_payable, self.c, "file_appeal", APPEAL_BOND, bid)

    def test_parties_never_enter_the_pool(self):
        register_jurors(self.c, 5)
        set_caller(PARTY_A_ADDRESS)
        call_payable(self.c, "register_juror", 5000)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        self.assertEqual(self.c.count_eligible_jurors(bid), 5)

    def test_jurors_registered_after_acceptance_are_not_eligible(self):
        register_jurors(self.c, 5)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        set_caller(JUROR_ADDRESSES[9])
        call_payable(self.c, "register_juror", 5000)
        self.assertEqual(self.c.count_eligible_jurors(bid), 5)

    def test_pool_holds_block_unstaking_until_the_draw(self):
        bid = appealed(self.c)
        set_caller(JUROR_ADDRESSES[0])
        expect_user_error(self, self.c.unstake_juror, "1")
        draw_jury(self.c, bid)
        holds = [json.loads(self.c.get_juror(a))["pool_holds"] for a in JUROR_ADDRESSES[:6]]
        self.assertEqual(holds, [0] * 6)

    def test_the_draw_round_is_after_the_appeal_window(self):
        bid = appealed(self.c)
        b = get(self.c, bid)
        deadline = int(datetime.datetime.fromisoformat(b["appeal_deadline"]).timestamp())
        round_time = self.c.DRAND_GENESIS + (b["appeal"]["draw_round"] - 1) * self.c.DRAND_PERIOD
        self.assertGreater(round_time, deadline)


class TestDraw(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_draw_selects_a_full_jury_from_the_pool(self):
        bid = jury_case(self.c)
        b = get(self.c, bid)
        self.assertEqual(b["status"], "jury_voting")
        self.assertEqual(len(set(selected(self.c, bid))), self.c.JURY_SIZE)

    def test_draw_is_rejected_inside_the_appeal_window(self):
        bid = appealed(self.c)
        expect_user_error(self, self.c.draw_jury, bid)

    def test_draw_without_a_beacon_fails_cleanly(self):
        bid = appealed(self.c)
        pass_appeal_window(self.c, bid)
        with patch.object(gl.nondet.web, "render", side_effect=RuntimeError("down")):
            expect_user_error(self, self.c.draw_jury, bid)
        self.assertEqual(get(self.c, bid)["status"], "appeal_filed")

    def test_draw_rejects_a_beacon_for_a_different_round(self):
        bid = appealed(self.c)
        pass_appeal_window(self.c, bid)
        page = json.dumps({"round": 1, "randomness": "abc"})
        with patch.object(gl.nondet.web, "render", return_value=page):
            expect_user_error(self, self.c.draw_jury, bid)

    def test_draw_before_the_round_exists_is_rejected(self):
        bid = appealed(self.c)
        CLOCK.set(datetime.datetime.fromisoformat(get(self.c, bid)["appeal_deadline"]) + datetime.timedelta(seconds=1))
        expect_user_error(self, self.c.draw_jury, bid)

    def test_selection_replays_to_the_same_jury(self):
        bid = jury_case(self.c)
        self.assertTrue(json.loads(self.c.verify_jury_selection(bid))["match"])

    def test_tampered_selection_fails_the_replay(self):
        bid = jury_case(self.c)
        b = get(self.c, bid)
        b["jury"]["selected_jurors"] = list(reversed(b["jury"]["selected_jurors"]))[:4] + [JUROR_ADDRESSES[11]]
        self.c.jobs[bid] = json.dumps(b)
        self.assertFalse(json.loads(self.c.verify_jury_selection(bid))["match"])

    def test_selection_view_needs_a_jury(self):
        bid = appealed(self.c)
        expect_user_error(self, self.c.verify_jury_selection, bid)

    def test_different_beacons_can_select_different_juries(self):
        seen = set()
        for beacon in ("a", "b", "c", "d", "e", "f"):
            c = make_contract()
            bid = appealed(c, jurors=9)
            draw_jury(c, bid, beacon=beacon)
            seen.add(tuple(sorted(selected(c, bid))))
        self.assertGreater(len(seen), 1)

    def test_same_beacon_gives_the_same_jury(self):
        runs = []
        for _ in range(2):
            c = make_contract()
            bid = appealed(c, jurors=9)
            draw_jury(c, bid, beacon="same")
            runs.append(selected(c, bid))
        self.assertEqual(runs[0], runs[1])

    def test_selected_jurors_are_locked_until_settlement(self):
        bid = jury_case(self.c)
        first = selected(self.c, bid)[0]
        set_caller(first)
        expect_user_error(self, self.c.unstake_juror, "1")
        self.assertEqual(json.loads(self.c.get_juror(first))["locked_cases"], 1)

    def test_a_juror_outside_the_jury_cannot_commit(self):
        bid = jury_case(self.c, jurors=8)
        outsider = [a for a in JUROR_ADDRESSES[:8] if a.lower() not in selected(self.c, bid)][0]
        set_caller(outsider)
        expect_user_error(self, self.c.commit_vote, bid, commit_hash(N, "s", bid, outsider))


class TestCommitReveal(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.bid = jury_case(self.c)
        self.j = selected(self.c, self.bid)

    def test_commit_then_reveal(self):
        vote(self.c, self.bid, self.j[0], N)
        open_reveal(self.c, self.bid)
        set_caller(self.j[0])
        self.assertEqual(self.c.reveal_vote(self.bid, N, "s"), "revealed")
        self.assertEqual(get(self.c, self.bid)["jury"]["reveals"][self.j[0]], N)

    def test_commit_must_be_a_sha256_hex_string(self):
        set_caller(self.j[0])
        for bad in ("", "abc", "z" * 64, "0" * 63, "0" * 65):
            with self.subTest(bad=bad):
                expect_user_error(self, self.c.commit_vote, self.bid, bad)

    def test_double_commit_is_rejected(self):
        vote(self.c, self.bid, self.j[0], N)
        expect_user_error(self, vote, self.c, self.bid, self.j[0], D)

    def test_commit_after_the_window_is_rejected(self):
        open_reveal(self.c, self.bid)
        expect_user_error(self, vote, self.c, self.bid, self.j[0], N)

    def test_reveal_before_the_window_opens_is_rejected(self):
        vote(self.c, self.bid, self.j[0], N)
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "s")

    def test_reveal_after_the_window_is_rejected(self):
        vote(self.c, self.bid, self.j[0], N)
        close_reveal(self.c, self.bid)
        set_caller(self.j[0])
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "s")

    def test_reveal_without_a_commit_is_rejected(self):
        open_reveal(self.c, self.bid)
        set_caller(self.j[0])
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "s")

    def test_reveal_with_the_wrong_vote_or_salt_is_rejected(self):
        vote(self.c, self.bid, self.j[0], N)
        open_reveal(self.c, self.bid)
        set_caller(self.j[0])
        expect_user_error(self, self.c.reveal_vote, self.bid, D, "s")
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "other")

    def test_reveal_of_an_invalid_outcome_is_rejected(self):
        set_caller(self.j[0])
        self.c.commit_vote(self.bid, commit_hash("MAYBE", "s", self.bid, self.j[0]))
        open_reveal(self.c, self.bid)
        expect_user_error(self, self.c.reveal_vote, self.bid, "MAYBE", "s")

    def test_double_reveal_is_rejected(self):
        vote(self.c, self.bid, self.j[0], N)
        open_reveal(self.c, self.bid)
        set_caller(self.j[0])
        self.c.reveal_vote(self.bid, N, "s")
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "s")

    def test_a_commit_copied_from_another_juror_cannot_be_revealed(self):
        vote(self.c, self.bid, self.j[0], N)
        copied = get(self.c, self.bid)["jury"]["commits"][self.j[0]]
        set_caller(self.j[1])
        self.c.commit_vote(self.bid, copied)
        open_reveal(self.c, self.bid)
        set_caller(self.j[1])
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "s")

    def test_a_commit_is_bound_to_the_case(self):
        self.assertNotEqual(commit_hash(N, "s", "job_1", self.j[0]), commit_hash(N, "s", "job_2", self.j[0]))

    def test_oversized_salt_is_rejected(self):
        vote(self.c, self.bid, self.j[0], N)
        open_reveal(self.c, self.bid)
        set_caller(self.j[0])
        expect_user_error(self, self.c.reveal_vote, self.bid, N, "x" * 257)

    def test_all_four_outcomes_are_valid_votes(self):
        self.assertEqual(set(self.c.VALID_OUTCOMES), {D, P, N, U})


class TestSettleJury(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_settling_before_the_reveal_window_closes_is_rejected(self):
        bid = jury_case(self.c)
        set_caller(STRANGER_ADDRESS)
        expect_user_error(self, self.c.settle_jury, bid)

    def test_verified_majority_for_the_tier1_outcome_confirms_it(self):
        bid = jury_case(self.c)
        b = run_vote(self.c, bid, [N] * 5)
        self.assertEqual((b["status"], b["final_outcome"]), ("settled", N))
        self.assertTrue(b["jury"]["verification_passed"])
        # The sponsor receives the pot plus the half of the appeal bond that is not the jury fee.
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), REWARD + BOND + (APPEAL_BOND - JURY_FEE))

    def test_a_confirmed_appeal_pays_the_jury_fee_and_compensates_the_other_party(self):
        bid = jury_case(self.c)
        run_vote(self.c, bid, [N] * 5)
        paid = sum(pending(self.c, a) for a in selected(self.c, bid))
        self.assertEqual(paid, JURY_FEE)
        self.assertEqual(pending(self.c, PARTY_B_ADDRESS), 0)

    def test_an_overturn_is_verified_and_returns_the_bond(self):
        bid = jury_case(self.c, tier1=U, pages=CLOSED_PAGES, model={"outcome": U})
        b = run_vote(self.c, bid, [N] * 5, model={"outcome": N})
        self.assertEqual((b["tier1_outcome"], b["final_outcome"]), (U, N))
        self.assertEqual(pending(self.c, PARTY_B_ADDRESS), APPEAL_BOND - JURY_FEE)
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), REWARD + BOND)
        self.assertEqual(sum(pending(self.c, a) for a in selected(self.c, bid)), JURY_FEE)

    def test_the_jury_cannot_force_a_delivery_without_a_proven_review(self):
        bid = jury_case(self.c)
        b = run_vote(self.c, bid, [D] * 5, model={"outcome": D})
        self.assertFalse(b["jury"]["verification_passed"])
        self.assertEqual(b["final_outcome"], N)
        self.assertEqual(pending(self.c, PARTY_B_ADDRESS), APPEAL_BOND)
        self.assertEqual(self.c.get_review_claim(REPO, 7, 1001), "")

    def test_an_unverified_majority_does_not_slash_dissenters(self):
        bid = jury_case(self.c)
        run_vote(self.c, bid, [D] * 5, model={"outcome": D})
        for addr in selected(self.c, bid):
            self.assertEqual(json.loads(self.c.get_juror(addr))["minority_votes"], 0)

    def test_a_tie_falls_back_to_tier1_and_refunds_the_bond(self):
        bid = jury_case(self.c)
        b = run_vote(self.c, bid, [N, N, D, D, None])
        self.assertEqual(b["final_outcome"], N)
        self.assertIsNone(b["jury"]["provisional_outcome"])
        self.assertEqual(pending(self.c, PARTY_B_ADDRESS), APPEAL_BOND)

    def test_too_few_reveals_fall_back_to_tier1(self):
        bid = jury_case(self.c)
        b = run_vote(self.c, bid, [N, N, None, None, None])
        self.assertIsNone(b["jury"]["provisional_outcome"])
        self.assertEqual(b["final_outcome"], N)

    def test_non_revealers_are_slashed_and_the_pool_goes_to_revealers(self):
        bid = jury_case(self.c)
        j = selected(self.c, bid)
        before = {a: int(json.loads(self.c.get_juror(a))["stake"]) for a in j}
        run_vote(self.c, bid, [N, N, N, N, None])
        silent = json.loads(self.c.get_juror(j[4]))
        self.assertLess(int(silent["stake"]), before[j[4]])
        self.assertEqual(silent["non_reveals"], 1)
        slashed = before[j[4]] - int(silent["stake"])
        gained = sum(pending(self.c, a) for a in j[:4])
        self.assertEqual(gained, JURY_FEE + slashed)

    def test_minority_voters_are_slashed_on_a_verified_majority(self):
        bid = jury_case(self.c)
        j = selected(self.c, bid)
        before = int(json.loads(self.c.get_juror(j[4]))["stake"])
        run_vote(self.c, bid, [N, N, N, N, D])
        rec = json.loads(self.c.get_juror(j[4]))
        self.assertEqual(int(rec["stake"]), before - before * 1000 // 10000)
        self.assertEqual(rec["reputation"], -2)

    def test_majority_voters_gain_reputation(self):
        bid = jury_case(self.c)
        run_vote(self.c, bid, [N] * 5)
        for addr in selected(self.c, bid):
            self.assertEqual(json.loads(self.c.get_juror(addr))["reputation"], 2)

    def test_jurors_are_unlocked_after_settlement(self):
        bid = jury_case(self.c)
        run_vote(self.c, bid, [N] * 5)
        for addr in selected(self.c, bid):
            self.assertEqual(json.loads(self.c.get_juror(addr))["locked_cases"], 0)

    def test_settling_twice_is_rejected(self):
        bid = jury_case(self.c)
        run_vote(self.c, bid, [N] * 5)
        expect_user_error(self, self.c.settle_jury, bid)

    def test_money_is_conserved_through_a_full_jury_case(self):
        bid = jury_case(self.c)
        j = selected(self.c, bid)
        stake_before = sum(int(json.loads(self.c.get_juror(a))["stake"]) for a in JUROR_ADDRESSES[:6])
        run_vote(self.c, bid, [N, N, N, D, None])
        ledger = pending(self.c, PARTY_A_ADDRESS) + pending(self.c, PARTY_B_ADDRESS)
        ledger += sum(pending(self.c, a) for a in JUROR_ADDRESSES[:6])
        stake_after = sum(int(json.loads(self.c.get_juror(a))["stake"]) for a in JUROR_ADDRESSES[:6])
        self.assertEqual(ledger + stake_after, REWARD + BOND + APPEAL_BOND + stake_before)

    def test_a_partial_ruling_keeps_its_review_claim_and_the_jury_cannot_award_breach(self):
        register_jurors(self.c, 6)
        bid = ruled_job(self.c, pages=pages_of(review_json()), model={"outcome": P})
        self.assertEqual(self.c.get_review_claim(REPO, 7, 1001), bid)
        self.assertEqual(json.loads(self.c.get_jury_options(bid)), sorted([D, P, U]))

    def test_an_overturn_to_partial_claims_the_review(self):
        pages = pages_of(short_review_json())
        bid = jury_case(self.c, tier1=U, pages=pages, model=None)
        self.assertEqual(self.c.get_review_claim(REPO, 7, 1001), "")
        b = run_vote(self.c, bid, [P] * 5)
        self.assertEqual(b["final_outcome"], P)
        self.assertEqual(self.c.get_review_claim(REPO, 7, 1001), bid)


class TestRecoverStuckAppeal(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_filed_but_never_drawn_can_be_recovered_after_the_timeout(self):
        bid = appealed(self.c)
        b = get(self.c, bid)
        set_caller(STRANGER_ADDRESS)
        CLOCK.set(datetime.datetime.fromisoformat(b["appeal_deadline"]) + datetime.timedelta(days=7))
        expect_user_error(self, self.c.recover_stuck_appeal, bid)
        CLOCK.advance(1)
        self.c.recover_stuck_appeal(bid)
        done = get(self.c, bid)
        self.assertEqual((done["status"], done["final_outcome"]), ("settled", N))
        self.assertEqual(pending(self.c, PARTY_B_ADDRESS), APPEAL_BOND)
        for addr in JUROR_ADDRESSES[:6]:
            self.assertEqual(json.loads(self.c.get_juror(addr))["pool_holds"], 0)

    def test_an_unsettled_jury_can_be_recovered_and_non_revealers_are_slashed(self):
        bid = jury_case(self.c)
        j = selected(self.c, bid)
        commit_all(self.c, bid, [N] * 5)
        reveal_all(self.c, bid, [N, N, None, None, None])
        deadline = datetime.datetime.fromisoformat(get(self.c, bid)["jury"]["reveal_deadline"])
        CLOCK.set(deadline + datetime.timedelta(days=7, seconds=1))
        set_caller(STRANGER_ADDRESS)
        self.c.recover_stuck_appeal(bid)
        self.assertEqual(get(self.c, bid)["final_outcome"], N)
        self.assertEqual(json.loads(self.c.get_juror(j[4]))["non_reveals"], 1)
        self.assertEqual(json.loads(self.c.get_juror(j[0]))["locked_cases"], 0)

    def test_recovery_is_rejected_for_other_statuses(self):
        register_jurors(self.c, 6)
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        CLOCK.advance(30 * DAY)
        expect_user_error(self, self.c.recover_stuck_appeal, bid)


class TestJurorStaking(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_registering_below_the_minimum_is_rejected(self):
        set_caller(JUROR_ADDRESSES[0])
        expect_user_error(self, call_payable, self.c, "register_juror", 0)

    def test_topping_up_adds_to_the_stake(self):
        set_caller(JUROR_ADDRESSES[0])
        call_payable(self.c, "register_juror", 100)
        call_payable(self.c, "register_juror", 50)
        self.assertEqual(json.loads(self.c.get_juror(JUROR_ADDRESSES[0]))["stake"], "150")

    def test_unstake_moves_funds_to_the_withdrawable_balance(self):
        set_caller(JUROR_ADDRESSES[0])
        call_payable(self.c, "register_juror", 100)
        self.c.unstake_juror("40")
        self.assertEqual(pending(self.c, JUROR_ADDRESSES[0]), 40)
        self.assertEqual(json.loads(self.c.get_juror(JUROR_ADDRESSES[0]))["stake"], "60")

    def test_invalid_unstake_amounts_are_rejected(self):
        set_caller(JUROR_ADDRESSES[0])
        call_payable(self.c, "register_juror", 100)
        for amount in ("0", "-1", "101"):
            with self.subTest(amount=amount):
                expect_user_error(self, self.c.unstake_juror, amount)

    def test_selection_weight_is_capped_and_reputation_scaled(self):
        weight = self.c._selection_weight
        self.assertEqual(weight({"stake": "1000", "reputation": 0}), 1000)
        self.assertEqual(weight({"stake": "1000", "reputation": 50}), 1500)
        self.assertEqual(weight({"stake": "1000", "reputation": -50}), 500)
        self.assertEqual(weight({"stake": str(10 ** 30), "reputation": 0}), 10 ** 24)


class TestWithdraw(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_withdraw_pays_the_whole_balance_once(self):
        set_caller(JUROR_ADDRESSES[0])
        call_payable(self.c, "register_juror", 100)
        self.c.unstake_juror("100")
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1]["value"], 100)
        self.assertEqual(pending(self.c, JUROR_ADDRESSES[0]), 0)
        expect_user_error(self, self.c.withdraw)

    def test_withdraw_with_nothing_owed_is_rejected(self):
        set_caller(STRANGER_ADDRESS)
        expect_user_error(self, self.c.withdraw)

    def test_a_withdrawal_pays_the_caller_not_anyone_else(self):
        set_caller(JUROR_ADDRESSES[0])
        call_payable(self.c, "register_juror", 100)
        self.c.unstake_juror("100")
        set_caller(STRANGER_ADDRESS)
        expect_user_error(self, self.c.withdraw)
        self.assertEqual(pending(self.c, JUROR_ADDRESSES[0]), 100)

    def test_party_payout_can_be_withdrawn_after_settlement(self):
        bid = ruled_job(self.c, pages=CLOSED_PAGES, model={"outcome": N})
        pass_appeal_window(self.c, bid)
        set_caller(STRANGER_ADDRESS)
        self.c.settle_unappealed(bid)
        set_caller(PARTY_A_ADDRESS)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1]["value"], REWARD + BOND)
        self.assertEqual(gl.evm.transfers[-1]["to"].lower(), PARTY_A_ADDRESS)


if __name__ == "__main__":
    unittest.main()
