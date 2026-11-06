import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    PeerLens, gl, u256, m, CLOCK, BASE, BASE_EPOCH, DAY, DEFAULT_DEADLINE, DEADLINE_EPOCH,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, REPO, PR_NUMBER, MIN_CHARS, LOGIN, REWARD, BOND, BOND_BPS,
    make_contract, set_caller, call_payable, create_job, accept_job, get, pending, iso,
    expect_user_error,
)


class TestCreateRecordsAnchors(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_anchors_are_recorded_on_chain(self):
        b = get(self.c, create_job(self.c))
        self.assertEqual(b["status"], "posted")
        self.assertEqual(b["party_a"], PARTY_A_ADDRESS)
        self.assertEqual(b["party_b"], PARTY_B_ADDRESS)
        self.assertEqual(b["repo"], REPO)
        self.assertEqual(b["repo_key"], REPO.lower())
        self.assertEqual(b["pr_number"], PR_NUMBER)
        self.assertEqual(b["min_review_chars"], MIN_CHARS)
        self.assertEqual(b["reviewer_login"], LOGIN)
        self.assertEqual(b["reviewer_key"], LOGIN.lower())
        self.assertEqual(b["deadline"], iso(DEFAULT_DEADLINE))

    def test_evidence_domain_is_fixed_to_the_github_api(self):
        b = get(self.c, create_job(self.c))
        self.assertEqual(b["allowed_evidence_domains"], ["api.github.com"])

    def test_the_caller_cannot_choose_an_evidence_domain(self):
        names = PeerLens.create_job.__code__.co_varnames[: PeerLens.create_job.__code__.co_argcount]
        self.assertFalse([n for n in names if "domain" in n or "url" in n or "host" in n])

    def test_time_anchors_are_integer_epochs(self):
        b = get(self.c, create_job(self.c))
        self.assertIsInstance(b["created_epoch"], int)
        self.assertIsInstance(b["deadline_epoch"], int)
        self.assertEqual(b["created_epoch"], BASE_EPOCH)
        self.assertEqual(b["deadline_epoch"], DEADLINE_EPOCH)

    def test_money_fields(self):
        b = get(self.c, create_job(self.c))
        self.assertEqual(b["reward_amount"], str(REWARD))
        self.assertEqual(b["bond_amount"], str(BOND))
        self.assertEqual(b["bond_bps"], BOND_BPS)
        self.assertEqual(b["partial_reviewer_bps"], 5000)
        self.assertEqual(int(self.c.balance), REWARD)

    def test_bond_follows_bond_bps(self):
        b = get(self.c, create_job(self.c, bond_bps=2500))
        self.assertEqual(b["bond_amount"], str(REWARD * 2500 // 10000))
        b = get(self.c, create_job(self.c, bond_bps=10000))
        self.assertEqual(b["bond_amount"], str(REWARD))

    def test_ids_increment_and_counter(self):
        self.assertEqual(create_job(self.c), "job_0")
        self.assertEqual(create_job(self.c), "job_1")
        self.assertEqual(self.c.total_jobs(), 2)

    def test_inputs_are_trimmed(self):
        b = get(self.c, create_job(self.c, repo="  acme/widgets  ", login=" OctoDev "))
        self.assertEqual((b["repo"], b["reviewer_login"]), (REPO, LOGIN))

    def test_mixed_case_repo_and_login_keep_the_original_and_a_lowercase_key(self):
        b = get(self.c, create_job(self.c, repo="Acme/Widgets", login="OCTODEV"))
        self.assertEqual((b["repo"], b["repo_key"]), ("Acme/Widgets", "acme/widgets"))
        self.assertEqual((b["reviewer_login"], b["reviewer_key"]), ("OCTODEV", "octodev"))

    def test_new_job_has_no_outcome_and_no_claim(self):
        b = get(self.c, create_job(self.c))
        for key in ("tier1_outcome", "final_outcome", "evidence_snapshot", "appeal", "jury"):
            self.assertIsNone(b[key])
        self.assertEqual(b["claimed_review_key"], "")
        self.assertFalse(b["payout_settled"])


class TestCreateValidation(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def fails(self, **over):
        with self.assertRaises(gl.vm.UserError):
            create_job(self.c, **over)
        self.assertEqual(self.c.total_jobs(), 0)
        # A rejected payable call never strands GEN: everything the contract
        # holds is owed back to the sender and withdrawable.
        self.assertEqual(int(self.c.balance), int(self.c.get_pending_withdrawal(PARTY_A_ADDRESS)))

    def test_reward_below_minimum(self):
        with patch.object(PeerLens, "MIN_STAKE", u256(100)):
            self.fails(reward=99)
            create_job(self.c, reward=100)

    def test_bond_below_minimum(self):
        with patch.object(PeerLens, "MIN_BOND", u256(100)):
            self.fails(reward=1000, bond_bps=500)
            create_job(self.c, reward=2000, bond_bps=500)

    def test_zero_reward(self):
        self.fails(reward=0)

    def test_same_wallet(self):
        self.fails(party_b=PARTY_A_ADDRESS)
        self.fails(party_b=PARTY_A_ADDRESS.upper().replace("0X", "0x"))

    def test_invalid_wallet(self):
        for bad in ("", "0x123", "not-an-address", "0x" + "zz" * 20, "22" * 20):
            with self.subTest(bad=bad):
                self.fails(party_b=bad)

    def test_bad_repo(self):
        for bad in ("", "acme", "acme/", "/widgets", "a/b/c", "acme/wid gets", "../x", "acme/..", "./x", "a b/c",
                    "acme/widgets?x", "acme/widgets#1", "acme\\widgets", "x" * 101 + "/y", "x/" + "y" * 101):
            with self.subTest(bad=bad):
                self.fails(repo=bad)

    def test_good_repos(self):
        for good in ("a/b", "Acme/Widgets.js", "a-b/c_d.e", "x" * 100 + "/" + "y" * 100):
            with self.subTest(good=good):
                create_job(self.c, repo=good)

    def test_bad_pr_numbers(self):
        for bad in (0, -1, 10 ** 10, True, "5", 4.0, None):
            with self.subTest(bad=bad):
                self.fails(pr=bad)

    def test_good_pr_numbers(self):
        for good in (1, 7, 9999999999):
            with self.subTest(good=good):
                create_job(self.c, pr=good)

    def test_bad_min_review_chars(self):
        for bad in (0, 19, 5001, -5, True, "100", 100.0, None):
            with self.subTest(bad=bad):
                self.fails(min_chars=bad)

    def test_good_min_review_chars(self):
        for good in (20, 100, 5000):
            with self.subTest(good=good):
                self.assertEqual(get(self.c, create_job(self.c, min_chars=good))["min_review_chars"], good)

    def test_bad_logins(self):
        for bad in ("", "-a", "a-", "a b", "a_b", "x" * 40, "octo@dev", "octo.dev", "bot[bot]", "é"):
            with self.subTest(bad=bad):
                self.fails(login=bad)

    def test_good_logins(self):
        for good in ("a", "octo-dev", "x" * 39, "A1", "a-b-c"):
            with self.subTest(good=good):
                create_job(self.c, login=good)

    def test_bond_bps_bounds(self):
        for bad in (499, 0, -1, 10001, True, "2000", None):
            with self.subTest(bad=bad):
                self.fails(bond_bps=bad)
        for good in (500, 10000):
            create_job(self.c, bond_bps=good)

    def test_partial_bps_bounds(self):
        for bad in (0, -1, 10000, True, "5000", None):
            with self.subTest(bad=bad):
                self.fails(partial_bps=bad)
        for good in (1, 9999):
            create_job(self.c, partial_bps=good)

    def test_empty_or_oversized_text(self):
        self.fails(terms="")
        self.fails(terms="   ")
        self.fails(criteria="")
        self.fails(terms="x" * 2001)
        self.fails(criteria="x" * 2001)
        create_job(self.c, terms="x" * 2000, criteria="y" * 2000)

    def test_deadline_too_soon(self):
        self.fails(deadline=BASE + datetime.timedelta(minutes=59))
        create_job(self.c, deadline=BASE + datetime.timedelta(hours=1))

    def test_deadline_too_far(self):
        self.fails(deadline=BASE + datetime.timedelta(days=91))
        create_job(self.c, deadline=BASE + datetime.timedelta(days=90))

    def test_deadline_in_the_past(self):
        self.fails(deadline=BASE - datetime.timedelta(days=1))

    def test_deadline_not_a_timestamp(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "create_job", REWARD, PARTY_B_ADDRESS, REPO, PR_NUMBER, LOGIN, MIN_CHARS, "t", "c", "tomorrow", 2000, 5000)

    def test_naive_deadline_is_read_as_utc(self):
        set_caller(PARTY_A_ADDRESS)
        naive = (BASE + datetime.timedelta(days=2)).replace(tzinfo=None).isoformat()
        bid = call_payable(self.c, "create_job", REWARD, PARTY_B_ADDRESS, REPO, PR_NUMBER, LOGIN, MIN_CHARS, "t", "c", naive, 2000, 5000)
        self.assertEqual(get(self.c, bid)["deadline_epoch"], BASE_EPOCH + 2 * DAY)

    def test_deadline_with_offset_is_converted_to_utc(self):
        local = (BASE + datetime.timedelta(days=2)).astimezone(datetime.timezone(datetime.timedelta(hours=3, minutes=30)))
        b = get(self.c, create_job(self.c, deadline=local))
        self.assertEqual(b["deadline_epoch"], BASE_EPOCH + 2 * DAY)
        self.assertTrue(b["deadline"].endswith("+00:00"))


class TestAcceptAndCancel(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.bid = create_job(self.c)

    def test_only_the_designated_reviewer_accepts(self):
        for caller in (STRANGER_ADDRESS, PARTY_A_ADDRESS):
            set_caller(caller)
            with self.assertRaises(gl.vm.UserError):
                call_payable(self.c, "accept_job", BOND, self.bid)
        self.assertEqual(get(self.c, self.bid)["status"], "posted")

    def test_bond_must_match_exactly(self):
        for wrong in (0, BOND - 1, BOND + 1, REWARD):
            set_caller(PARTY_B_ADDRESS)
            with self.assertRaises(gl.vm.UserError):
                call_payable(self.c, "accept_job", wrong, self.bid)
        refunded = BOND - 1 + BOND + 1 + REWARD
        self.assertEqual(int(self.c.get_pending_withdrawal(PARTY_B_ADDRESS)), refunded)
        self.assertEqual(int(self.c.balance), REWARD + refunded)
        self.assertEqual(get(self.c, self.bid)["status"], "posted")

    def test_accept_moves_to_active_and_sets_windows(self):
        accept_job(self.c, self.bid)
        b = get(self.c, self.bid)
        self.assertEqual(b["status"], "active")
        self.assertEqual(b["accepted_at"], iso(BASE))
        self.assertEqual(b["evidence_deadline"], iso(DEFAULT_DEADLINE + datetime.timedelta(days=3)))
        self.assertEqual(int(self.c.balance), REWARD + BOND)

    def test_accept_after_the_deadline_is_rejected(self):
        CLOCK.set(DEFAULT_DEADLINE + datetime.timedelta(seconds=1))
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "accept_job", BOND, self.bid)

    def test_accept_twice_is_rejected(self):
        accept_job(self.c, self.bid)
        with self.assertRaises(gl.vm.UserError):
            accept_job(self.c, self.bid)

    def test_accept_unknown_job(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            call_payable(self.c, "accept_job", BOND, "job_99")

    def test_sponsor_can_cancel_before_acceptance_and_is_refunded(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_job(self.bid)
        self.assertEqual(get(self.c, self.bid)["status"], "cancelled")
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), REWARD)

    def test_only_the_sponsor_cancels(self):
        for caller in (PARTY_B_ADDRESS, STRANGER_ADDRESS):
            set_caller(caller)
            with self.assertRaises(gl.vm.UserError):
                self.c.cancel_job(self.bid)

    def test_cancel_after_acceptance_is_rejected(self):
        accept_job(self.c, self.bid)
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_job(self.bid)

    def test_cancel_twice_does_not_double_refund(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_job(self.bid)
        with self.assertRaises(gl.vm.UserError):
            self.c.cancel_job(self.bid)
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), REWARD)

    def test_cancelled_job_cannot_be_accepted(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_job(self.bid)
        with self.assertRaises(gl.vm.UserError):
            accept_job(self.c, self.bid)

    def test_sponsor_can_cancel_after_the_deadline_passes_without_acceptance(self):
        CLOCK.set(DEFAULT_DEADLINE + datetime.timedelta(days=30))
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_job(self.bid)
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), REWARD)


class TestWithdraw(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.bid = create_job(self.c)
        set_caller(PARTY_A_ADDRESS)
        self.c.cancel_job(self.bid)

    def test_withdraw_pays_out_and_zeroes_the_ledger_first(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers, [{"to": PARTY_A_ADDRESS, "value": REWARD}])
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), 0)

    def test_second_withdraw_fails(self):
        set_caller(PARTY_A_ADDRESS)
        self.c.withdraw()
        with self.assertRaises(gl.vm.UserError):
            self.c.withdraw()
        self.assertEqual(len(gl.evm.transfers), 1)

    def test_withdraw_with_no_balance_fails(self):
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.withdraw()

    def test_withdraw_only_moves_the_callers_own_balance(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.withdraw()
        self.assertEqual(pending(self.c, PARTY_A_ADDRESS), REWARD)

    def test_ledger_is_zeroed_before_the_transfer_is_attempted(self):
        seen = []
        original = gl.evm.contract_interface

        class Probe:
            def __init__(self, address):
                self.address = address

            def emit_transfer(self, value):
                seen.append(pending(self.outer, PARTY_A_ADDRESS))

        Probe.outer = self.c
        with patch.object(m, "_Payee", Probe):
            set_caller(PARTY_A_ADDRESS)
            self.c.withdraw()
        self.assertEqual(seen, [0])


if __name__ == "__main__":
    unittest.main()
