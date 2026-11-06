import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
CONTRACT = open(os.path.join(ROOT, "contract.py"), encoding="utf-8").read()


def contract_methods():
    tree = ast.parse(CONTRACT)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PeerLens")
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}


def script():
    return re.search(r'<script type="module">(.*?)</script>', HTML, re.S).group(1)


class TestFrontend(unittest.TestCase):
    def test_single_file_with_esm_sh_latest(self):
        self.assertIn('from "https://esm.sh/genlayer-js@latest"', HTML)
        self.assertIn('from "https://esm.sh/genlayer-js@latest/chains"', HTML)
        self.assertEqual(len(re.findall(r"<script\b", HTML)), 1)
        self.assertNotIn("<link", HTML)

    def test_clients_use_only_chain_and_account(self):
        calls = re.findall(r"createClient\(\{([^}]*)\}\)", HTML)
        self.assertGreaterEqual(len(calls), 3)
        for body in calls:
            self.assertTrue(body.strip().startswith("chain: studionet"), body)
            self.assertNotIn("provider", body)
        for forbidden in ("initializeConsensusSmartContract", ".connect(", "provider:"):
            self.assertNotIn(forbidden, script())

    def test_hash_routes(self):
        for route in ('href="#/"', 'href="#/create"', 'href="#/jury"', 'href="#/withdraw"', "#/job/"):
            self.assertIn(route, HTML)
        self.assertIn('addEventListener("hashchange", route)', HTML)

    def test_explore_uses_the_list_view_not_manual_id_lookup(self):
        self.assertIn('"list_jobs"', HTML)
        self.assertIn('"get_stats"', HTML)
        self.assertIn('id="jobList"', HTML)
        self.assertNotIn("Enter job ID", HTML)

    def test_every_called_method_exists_in_the_contract(self):
        called = set(re.findall(r'(?:read|readJson)\("([a-z_]+)"', script()))
        called |= set(re.findall(r'write\([^,]+,\s*"[^"]*",\s*"([a-z_]+)"', script()))
        self.assertGreaterEqual(len(called), 15)
        self.assertEqual(called - contract_methods(), set())

    def test_every_write_method_is_reachable_from_the_ui(self):
        used = set(re.findall(r'"([a-z_]+)"', script()))
        for name in ("create_job", "accept_job", "cancel_job", "submit_evidence", "lock_evidence",
                     "adjudicate", "settle_unappealed", "expire_no_evidence", "expire_unruled",
                     "recover_stuck_appeal", "register_juror", "unstake_juror", "file_appeal", "draw_jury",
                     "commit_vote", "reveal_vote", "settle_jury", "withdraw"):
            self.assertIn(name, used)

    def test_create_argument_order_matches_the_contract(self):
        sig = re.search(r"def create_job\((.*?)\) -> str", CONTRACT, re.S).group(1)
        params = [p.strip().split(":")[0] for p in sig.split(",") if p.strip() and p.strip() != "self"]
        self.assertEqual(params, ["reviewer_wallet", "repo", "pr_number", "reviewer_login", "min_review_chars",
                                  "job_terms", "review_criteria", "deadline_iso", "bond_bps",
                                  "partial_reviewer_bps"])
        call = re.search(r'"create_job",\s*\[(.*?)\], reward', script(), re.S).group(1)
        self.assertEqual([a.strip() for a in call.split(",")],
                         ["wallet", "repo", "pr", "login", "minChars", "terms", "criteria",
                          "deadline.toISOString()", "bondBps", "partial"])

    def test_page_text_never_uses_innerhtml(self):
        self.assertNotIn("innerHTML", HTML)
        self.assertNotIn("outerHTML", HTML)
        self.assertNotIn("document.write", HTML)

    def test_vote_options_cover_all_four_outcomes(self):
        for outcome in ("REVIEW_DELIVERED", "PARTIALLY_DELIVERED", "NOT_DELIVERED", "UNDETERMINED"):
            self.assertIn(outcome, script())

    def test_commit_hash_format_matches_the_contract(self):
        self.assertIn("`${sel.value}:${salt}:${me}:${id}`", script())
        self.assertIn('f"{vote}:{salt}:{caller}:{job_id}"', CONTRACT)

    def test_the_evidence_url_is_built_from_the_anchors(self):
        self.assertIn("https://api.github.com/repos/", HTML)
        self.assertIn("function reviewsEvidenceUrl", HTML)
        self.assertIn("/reviews?per_page=100", HTML)

    def test_an_unconfigured_contract_is_explained_not_called(self):
        self.assertIn("No contract is configured yet", HTML)

    def test_localstorage_is_always_guarded(self):
        for match in re.finditer(r"localStorage\.", script()):
            before = script()[max(0, match.start() - 200):match.start()]
            self.assertIn("try", before)

    def test_contract_address_comes_from_a_constant_or_override(self):
        self.assertIn("DEFAULT_CONTRACT_ADDRESS", HTML)
        self.assertIn('get("contract")', HTML)

    def test_no_secrets_are_embedded(self):
        self.assertNotRegex(HTML, r"0x[0-9a-fA-F]{64}")

    def test_mobile_viewport_and_dark_mode(self):
        self.assertIn('name="viewport"', HTML)
        self.assertIn("prefers-color-scheme: dark", HTML)

    def test_rejected_payments_are_shown_as_errors_with_the_refund(self):
        self.assertIn("result.rejected", script())
        self.assertIn("refunded_wei", script())

    def test_create_navigates_to_the_new_job(self):
        self.assertIn('location.hash = "#/job/" + id', script())

    def test_paging_never_shows_a_job_twice(self):
        self.assertIn("seen.has(r.job_id)", script())

    def test_ascii_only(self):
        HTML.encode("ascii")


if __name__ == "__main__":
    unittest.main()


class TestEnglishOnly(unittest.TestCase):
    def test_no_arabic_or_persian_characters_anywhere_in_the_repo(self):
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for name in files:
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                bad = [c for c in text if 0x0600 <= ord(c) <= 0x06FF or 0x0750 <= ord(c) <= 0x077F]
                self.assertEqual(bad, [], path)

    def test_required_repo_files_exist(self):
        for name in ("README.md", "ARCHITECTURE.md", "LICENSE", ".gitignore", "contract.py", "contract_deploy.py",
                     "index.html", os.path.join(".github", "workflows", "tests.yml")):
            self.assertTrue(os.path.exists(os.path.join(ROOT, name)), name)
