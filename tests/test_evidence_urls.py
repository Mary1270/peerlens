import unittest

from _bootstrap import (
    m, gl, anchors, CLOCK, REPO, PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS,
    make_contract, set_caller, active_job, get, reviews_url, expect_user_error,
)

B = anchors()
CANON = reviews_url()
BASE_URL = "https://api.github.com/repos/acme/widgets/pulls/7/reviews"


def item(url):
    return m._evidence_item(url, B)


class TestAcceptedSpellings(unittest.TestCase):
    def test_every_spelling_collapses_to_one_canonical_url(self):
        for url in (BASE_URL, BASE_URL + "?per_page=100", "  " + BASE_URL + "  ",
                    "HTTPS://api.github.com/repos/ACME/Widgets/pulls/7/reviews",
                    "https://API.GITHUB.COM/repos/acme/widgets/pulls/7/reviews?per_page=100"):
            with self.subTest(url=url):
                self.assertEqual(item(url), (CANON, "reviews", "7"))

    def test_the_canonical_url_is_built_from_the_anchors(self):
        self.assertEqual(CANON, BASE_URL + "?per_page=100")
        self.assertEqual(item(BASE_URL)[0], m._reviews_url(B))


class TestRejectedUrls(unittest.TestCase):
    def test_everything_else_is_rejected(self):
        bad = [
            "", "   ", "not a url", "ftp://api.github.com/repos/acme/widgets/pulls/7/reviews",
            "http://api.github.com/repos/acme/widgets/pulls/7/reviews",
            "https://github.com/acme/widgets/pull/7",
            "https://github.com/acme/widgets/pull/7#pullrequestreview-1",
            "https://api.github.com/repos/acme/widgets/pulls/7",
            "https://api.github.com/repos/acme/widgets/pulls/7/comments",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews/1001",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews/",
            "https://api.github.com/repos/acme/widgets/pulls/8/reviews",
            "https://api.github.com/repos/acme/widgets/pulls/07/reviews",
            "https://api.github.com/repos/acme/other/pulls/7/reviews",
            "https://api.github.com/repos/other/widgets/pulls/7/reviews",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews?per_page=50",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews?per_page=100&page=2",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews?page=2",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews?per_page=",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews?x",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews#frag",
            "https://api.github.com:443/repos/acme/widgets/pulls/7/reviews",
            "https://api.github.com:8443/repos/acme/widgets/pulls/7/reviews",
            "https://user@api.github.com/repos/acme/widgets/pulls/7/reviews",
            "https://user:pw@api.github.com/repos/acme/widgets/pulls/7/reviews",
            "https://api.github.com@evil.example/repos/acme/widgets/pulls/7/reviews",
            "https://api.github.com.evil.example/repos/acme/widgets/pulls/7/reviews",
            "https://evilapi.github.com/repos/acme/widgets/pulls/7/reviews",
            "https://raw.githubusercontent.com/acme/widgets/main/reviews.json",
            "https://api.github.com/search/issues?q=repo:acme/widgets",
            "https://api.github.com/repos/acme/widgets/pulls/7/reviews/../../pulls/8/reviews",
            "https://api.github.com//repos/acme/widgets/pulls/7/reviews",
            None, 5,
        ]
        for url in bad:
            with self.subTest(url=url):
                self.assertIsNone(m._evidence_item(url, B))

    def test_a_different_job_has_a_different_canonical_url(self):
        other = anchors(repo="Other/Repo", repo_key="other/repo", pr_number=99)
        self.assertEqual(m._evidence_item("https://api.github.com/repos/other/repo/pulls/99/reviews", other)[0],
                         "https://api.github.com/repos/Other/Repo/pulls/99/reviews?per_page=100")
        self.assertIsNone(m._evidence_item(BASE_URL, other))


class TestSubmitEvidence(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.bid = active_job(self.c)

    def test_a_party_can_submit_and_the_url_is_stored_canonically(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(self.bid, [BASE_URL])
        self.assertEqual(get(self.c, self.bid)["evidence_submissions"][PARTY_B_ADDRESS.lower()], [CANON])

    def test_spellings_of_the_same_record_collapse(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(self.bid, [BASE_URL, BASE_URL + "?per_page=100", CANON])
        self.assertEqual(get(self.c, self.bid)["evidence_submissions"][PARTY_B_ADDRESS.lower()], [CANON])

    def test_one_bad_item_rejects_the_whole_submission(self):
        set_caller(PARTY_B_ADDRESS)
        message = expect_user_error(self, self.c.submit_evidence, self.bid, [BASE_URL, "https://example.com/x"])
        self.assertIn("not allowed", message)
        self.assertEqual(get(self.c, self.bid)["evidence_submissions"], {})

    def test_empty_submissions_are_rejected(self):
        set_caller(PARTY_B_ADDRESS)
        for bad in ([], ["", "  "], "not a list", None):
            with self.subTest(bad=bad):
                with self.assertRaises(gl.vm.UserError):
                    self.c.submit_evidence(self.bid, bad)

    def test_overlong_urls_are_rejected(self):
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.submit_evidence(self.bid, [BASE_URL + "?per_page=100&" + "x" * 400])

    def test_strangers_cannot_submit(self):
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            self.c.submit_evidence(self.bid, [BASE_URL])

    def test_both_parties_submitting_freezes_one_item(self):
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(self.bid, [BASE_URL])
        set_caller(PARTY_A_ADDRESS)
        self.c.submit_evidence(self.bid, [CANON])
        job = get(self.c, self.bid)
        self.assertEqual(job["status"], "evidence_locked")
        self.assertEqual(job["evidence_snapshot"]["source_urls"], [CANON])
        self.assertEqual(job["evidence_snapshot"]["accepted_source_count"], 1)


if __name__ == "__main__":
    unittest.main()
