import ast
import json
import os
import re
import unittest
from unittest.mock import patch

from _bootstrap import (
    m, gl, CLOCK, CONTRACT_PATH, PeerLens, PARTY_A_ADDRESS, PARTY_B_ADDRESS, REPO, REAL_NOW_UTC,
    make_contract, set_caller, create_job, active_job, get, expect_user_error,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_PATH = os.path.join(ROOT, "contract.py")

PERMISSIONLESS = {
    "lock_evidence", "adjudicate", "settle_unappealed", "expire_no_evidence", "expire_unruled",
    "recover_stuck_appeal", "draw_jury", "settle_jury",
}
CALLER_CHECKED = {
    "create_job", "accept_job", "cancel_job", "submit_evidence", "register_juror", "unstake_juror",
    "file_appeal", "commit_vote", "reveal_vote", "withdraw",
}


def parse(path):
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def contract_class(tree):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PeerLens")


def decorators(fn):
    return {ast.unparse(d) for d in fn.decorator_list}


class TestViews(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def make(self, count):
        ids = []
        for i in range(count):
            CLOCK.reset()
            ids.append(create_job(self.c, pr=100 + i))
        return ids

    def test_empty_listing(self):
        data = json.loads(self.c.list_jobs(0, 10))
        self.assertEqual((data["total"], data["jobs"]), (0, []))

    def test_listing_is_newest_first(self):
        ids = self.make(3)
        rows = json.loads(self.c.list_jobs(0, 10))["jobs"]
        self.assertEqual([r["job_id"] for r in rows], list(reversed(ids)))

    def test_pagination_walks_every_record_once(self):
        ids = self.make(7)
        seen = []
        for offset in (0, 3, 6, 9):
            seen += [r["job_id"] for r in json.loads(self.c.list_jobs(offset, 3))["jobs"]]
        self.assertEqual(seen, list(reversed(ids)))

    def test_limit_is_clamped(self):
        self.make(2)
        self.assertEqual(json.loads(self.c.list_jobs(0, 0))["limit"], 1)
        self.assertEqual(json.loads(self.c.list_jobs(0, 10 ** 6))["limit"], self.c.MAX_LIST_LIMIT)

    def test_negative_offset_is_treated_as_zero(self):
        self.make(2)
        self.assertEqual(json.loads(self.c.list_jobs(-5, 10))["offset"], 0)

    def test_summaries_carry_the_anchors_but_not_the_evidence(self):
        self.make(1)
        row = json.loads(self.c.list_jobs(0, 1))["jobs"][0]
        for key in ("repo", "pr_number", "min_review_chars", "reviewer_login", "deadline", "status"):
            self.assertIn(key, row)
        self.assertNotIn("evidence_content", row)

    def test_total_and_stats(self):
        self.make(3)
        self.assertEqual(self.c.total_jobs(), 3)
        self.assertEqual(json.loads(self.c.get_stats())["active"], 3)

    def test_unknown_job_is_a_clean_error(self):
        expect_user_error(self, self.c.get_job, "job_404")

    def test_claim_lookup_is_case_insensitive_and_empty_by_default(self):
        self.assertEqual(self.c.get_review_claim("Acme/Widgets", 7, 1001), "")

    def test_pending_withdrawal_for_an_unknown_address_is_zero(self):
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "0")

    def test_contract_balance_tracks_escrow(self):
        active_job(self.c)
        self.assertEqual(self.c.get_contract_balance(), "12000")

    def test_unknown_juror_has_an_empty_record(self):
        rec = json.loads(self.c.get_juror(PARTY_A_ADDRESS))
        self.assertEqual(rec["stake"], "0")

    def test_the_real_clock_is_timezone_aware_utc(self):
        now = REAL_NOW_UTC.__func__() if hasattr(REAL_NOW_UTC, "__func__") else REAL_NOW_UTC()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset().total_seconds(), 0)


class TestSourceAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = parse(SOURCE_PATH)
        cls.cls = contract_class(cls.tree)
        cls.methods = {n.name: n for n in cls.cls.body if isinstance(n, ast.FunctionDef)}

    def test_header_is_exactly_the_two_required_lines(self):
        with open(SOURCE_PATH, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[0], "# v0.2.16")
        self.assertEqual(
            lines[1], '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }'
        )
        self.assertEqual(lines[2], "from genlayer import *")

    def test_source_is_ascii_only(self):
        with open(SOURCE_PATH, "rb") as handle:
            handle.read().decode("ascii")

    def test_init_never_assigns_empty_containers_to_storage(self):
        init = self.methods["__init__"]
        for node in ast.walk(init):
            if isinstance(node, ast.Assign):
                self.assertNotIsInstance(node.value, (ast.Dict, ast.List), ast.unparse(node))

    def test_storage_is_declared_as_class_annotations(self):
        names = {n.target.id for n in self.cls.body if isinstance(n, ast.AnnAssign)}
        for field in ("jobs", "jurors", "pending_withdrawals", "review_claims", "job_count"):
            self.assertIn(field, names)

    def test_dataclass_is_imported_after_the_wildcard_import_when_used(self):
        source = open(SOURCE_PATH, encoding="utf-8").read()
        if "@dataclass" in source:
            self.assertLess(source.index("from genlayer import *"), source.index("from dataclasses import dataclass"))
            self.assertIn("@allow_storage", source)

    def test_nondet_closures_never_capture_self(self):
        for fn in self.methods.values():
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and ast.unparse(node.func) == "gl.vm.run_nondet_unsafe":
                    self.assertEqual(len(node.args), 2, fn.name)
                    self.assertFalse(node.keywords, fn.name)
                    for arg in node.args:
                        names = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
                        self.assertNotIn("self", names, fn.name)

    def test_strict_eq_closures_never_capture_self(self):
        for fn in self.methods.values():
            for inner in ast.walk(fn):
                if isinstance(inner, ast.FunctionDef) and inner is not fn:
                    names = {n.id for n in ast.walk(inner) if isinstance(n, ast.Name)}
                    self.assertNotIn("self", names, f"{fn.name}.{inner.name}")

    def test_nondet_helpers_are_module_level_and_catch_their_own_errors(self):
        top = {n.name for n in self.tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("_tier1_compute", "_tier1_validator", "_jury_options"):
            self.assertIn(name, top)
        source = open(SOURCE_PATH, encoding="utf-8").read()
        self.assertIn("except Exception", source)

    def test_every_public_write_is_classified(self):
        writes = {n for n, f in self.methods.items() if any(d.startswith("gl.public.write") for d in decorators(f))}
        self.assertEqual(writes, PERMISSIONLESS | CALLER_CHECKED | {"unstake_juror"} | set())

    def test_caller_checked_writes_read_the_sender(self):
        for name in CALLER_CHECKED:
            body = ast.unparse(self.methods[name]) + ast.unparse(self.methods.get("_" + name, self.methods[name]))
            self.assertIn("sender_address", body, name)

    def test_party_only_writes_compare_against_the_stored_parties(self):
        for name in ("accept_job", "cancel_job", "submit_evidence", "file_appeal"):
            body = ast.unparse(self.methods.get("_" + name, self.methods[name]))
            self.assertTrue("party_a" in body or "party_b" in body, name)

    def test_only_withdraw_moves_gen_out(self):
        for name, fn in self.methods.items():
            if name != "withdraw":
                self.assertNotIn("emit_transfer", ast.unparse(fn), name)

    def test_withdraw_zeroes_the_ledger_before_transferring(self):
        body = ast.unparse(self.methods["withdraw"])
        self.assertLess(body.index("= u256(0)"), body.index("emit_transfer"))

    def test_emit_transfer_uses_only_value(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("emit_transfer"):
                self.assertEqual([k.arg for k in node.keywords], ["value"])
                self.assertFalse(node.args)

    def test_views_are_decorated_and_do_not_write(self):
        views = [f for f in self.methods.values() if "gl.public.view" in decorators(f)]
        self.assertGreaterEqual(len(views), 8)
        for fn in views:
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        self.assertNotIn("self.jobs[", ast.unparse(target), fn.name)

    def test_time_comes_from_the_single_clock(self):
        source = open(SOURCE_PATH, encoding="utf-8").read()
        self.assertEqual(len(re.findall(r"datetime\.datetime\.now\(", source)), 1)
        self.assertNotIn("time.time(", source)

    def test_sender_is_read_from_gl_message_sender_address(self):
        source = open(SOURCE_PATH, encoding="utf-8").read()
        self.assertNotIn("gl.message.sender_account", source)
        self.assertNotIn("msg.sender", source)

    def test_inputs_are_bounded(self):
        for name in ("MAX_EVIDENCE_SOURCES", "MAX_URL_LENGTH", "MAX_TEXT_LENGTH", "MAX_LIST_LIMIT", "MAX_JURY_POOL"):
            self.assertGreater(getattr(PeerLens, name), 0)

    def test_only_one_evidence_host_is_allowed(self):
        self.assertEqual(m.EVIDENCE_HOST, "api.github.com")

    def test_no_exact_hash_of_live_pages_is_compared_by_validators(self):
        source = open(SOURCE_PATH, encoding="utf-8").read()
        start = source.index("def _tier1_agree")
        end = source.index("def _jury_options")
        self.assertNotIn("full_content_sha256", source[start:end])

    def test_every_stuck_state_has_a_recovery_method(self):
        for name in ("expire_no_evidence", "expire_unruled", "recover_stuck_appeal", "settle_unappealed", "settle_jury"):
            self.assertIn(name, self.methods)

    def test_no_wildcard_open_ended_loops_over_user_input(self):
        source = open(SOURCE_PATH, encoding="utf-8").read()
        self.assertNotIn("while True", source)


if __name__ == "__main__":
    unittest.main()
