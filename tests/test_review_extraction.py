import json
import unittest

from _bootstrap import (
    m, anchors, gh_ts, BASE_EPOCH, DAY, LOGIN, LONG_BODY, PR_NUMBER,
    review_json, other_review_json, pr_api_url,
)

B = anchors()


def extract(payload, b=None):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return m._extract_reviews(content, b or B)


class TestExtractionShape(unittest.TestCase):
    def test_a_review_list_is_reduced_to_the_reviewers_decisive_fields(self):
        facts = extract([review_json(1001)])
        self.assertEqual(facts["kind"], "reviews")
        self.assertTrue(facts["complete"])
        self.assertEqual(facts["reviewer_total"], 1)
        r = facts["reviews"][0]
        self.assertEqual(set(r), {"id", "state", "submitted_at", "body_len", "body_excerpt"})
        self.assertEqual(r["id"], 1001)
        self.assertEqual(r["state"], "COMMENTED")
        self.assertEqual(r["submitted_at"], BASE_EPOCH + DAY)
        self.assertEqual(r["body_len"], len(" ".join(LONG_BODY.split())))

    def test_volatile_fields_never_reach_the_facts(self):
        a = extract([review_json(1001, html_url="https://x/1", author_association="OWNER", commit_id="aaa")])
        b = extract([review_json(1001, html_url="https://x/2", author_association="NONE", commit_id="bbb")])
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_other_peoples_reviews_are_dropped(self):
        facts = extract([other_review_json(2001), review_json(1001), other_review_json(2002, login="third")])
        self.assertEqual([r["id"] for r in facts["reviews"]], [1001])
        self.assertEqual(facts["reviewer_total"], 1)

    def test_the_reviewer_login_is_case_insensitive(self):
        facts = extract([review_json(1001, user={"login": LOGIN.upper()})])
        self.assertEqual(facts["reviewer_total"], 1)

    def test_a_lookalike_login_is_not_the_reviewer(self):
        for login in (LOGIN + "2", "x" + LOGIN, LOGIN[:-1], LOGIN + "-bot"):
            with self.subTest(login=login):
                self.assertEqual(extract([review_json(1001, user={"login": login})])["reviewer_total"], 0)

    def test_a_review_of_another_pull_request_is_dropped(self):
        other = "https://api.github.com/repos/acme/widgets/pulls/8"
        self.assertEqual(extract([review_json(1001, pull_request_url=other)])["reviewer_total"], 0)
        self.assertEqual(extract([review_json(1001, pull_request_url=KeyError)])["reviewer_total"], 0)
        self.assertEqual(extract([review_json(1001, pull_request_url=None)])["reviewer_total"], 0)

    def test_the_pull_request_url_is_compared_case_insensitively(self):
        self.assertEqual(extract([review_json(1001, pull_request_url=pr_api_url().upper())])["reviewer_total"], 1)

    def test_a_deleted_user_is_skipped_not_fatal(self):
        facts = extract([review_json(1001, user=None), review_json(1002)])
        self.assertEqual([r["id"] for r in facts["reviews"]], [1002])

    def test_an_empty_list_is_complete_with_no_reviews(self):
        facts = extract([])
        self.assertEqual((facts["complete"], facts["reviewer_total"], facts["reviews"]), (True, 0, []))

    def test_a_full_page_is_not_complete(self):
        page = [other_review_json(5000 + i) for i in range(100)]
        self.assertFalse(extract(page)["complete"])
        self.assertTrue(extract(page[:99])["complete"])

    def test_more_than_a_page_is_not_a_review_list(self):
        self.assertIsNone(extract([other_review_json(5000 + i) for i in range(101)]))


class TestExtractionRejectsNonLists(unittest.TestCase):
    def test_non_json_and_wrong_shapes(self):
        for bad in ("", "not json", "null", "{}", '{"message": "Not Found"}', "42", '"text"', "<html></html>"):
            with self.subTest(bad=bad):
                self.assertIsNone(extract(bad))

    def test_a_list_with_a_non_object_is_not_a_review_list(self):
        self.assertIsNone(extract([review_json(1001), "x"]))
        self.assertIsNone(extract([review_json(1001), 5]))

    def test_every_entry_needs_a_positive_integer_id(self):
        for bad in (None, True, "5", 1.5, 0, -3, KeyError):
            with self.subTest(bad=bad):
                self.assertIsNone(extract([review_json(1001, id=bad)]))

    def test_viewer_chrome_around_the_json_is_tolerated(self):
        wrapped = "<pre>" + json.dumps([review_json(1001)]) + "</pre>"
        self.assertEqual(extract(wrapped)["reviewer_total"], 1)

    def test_rate_limit_message_is_unreadable(self):
        self.assertIsNone(extract('{"message": "API rate limit exceeded", "documentation_url": "x"}'))


class TestExtractionFields(unittest.TestCase):
    def test_state_is_upper_cased_and_bounded(self):
        self.assertEqual(extract([review_json(1001, state="approved")])["reviews"][0]["state"], "APPROVED")
        self.assertEqual(extract([review_json(1001, state="x" * 80)])["reviews"][0]["state"], "X" * 30)
        self.assertEqual(extract([review_json(1001, state=None)])["reviews"][0]["state"], "")

    def test_timestamps_must_be_strict_github_format(self):
        for bad in ("2026-03-02 12:00:00", "2026-03-02T12:00:00+00:00", "2026-03-02T12:00:00.000Z", "yesterday",
                    "2026-13-02T12:00:00Z", None, 1772000000, KeyError):
            with self.subTest(bad=bad):
                self.assertIsNone(extract([review_json(1001, submitted_at=bad)])["reviews"][0]["submitted_at"])

    def test_body_length_ignores_whitespace_padding(self):
        padded = "ok" + " " * 500 + "\n\n\t" * 50 + "fine"
        self.assertEqual(extract([review_json(1001, body=padded)])["reviews"][0]["body_len"], len("ok fine"))

    def test_missing_or_non_string_body_is_empty(self):
        for body in (None, KeyError, 5, ["x"]):
            with self.subTest(body=body):
                r = extract([review_json(1001, body=body)])["reviews"][0]
                self.assertEqual((r["body_len"], r["body_excerpt"]), (0, ""))

    def test_the_excerpt_is_bounded_and_the_length_is_not(self):
        r = extract([review_json(1001, body="word " * 1000)])["reviews"][0]
        self.assertEqual(len(r["body_excerpt"]), m.EXCERPT_CHARS)
        self.assertEqual(r["body_len"], len(("word " * 1000).strip()))

    def test_at_most_six_reviews_are_tracked_and_substantive_in_window_ones_win(self):
        late = [review_json(10 + i, submitted_at=gh_ts(BASE_EPOCH + 9 * DAY)) for i in range(8)]
        good = review_json(900)
        facts = extract(late + [good])
        ids = [r["id"] for r in facts["reviews"]]
        self.assertEqual(len(ids), m.MAX_TRACKED_REVIEWS)
        self.assertIn(900, ids)
        self.assertEqual(facts["reviewer_total"], 9)
        self.assertEqual(ids, sorted(ids))

    def test_the_result_is_stable_under_reordering(self):
        items = [review_json(1001), review_json(1002, state="APPROVED"), review_json(1003)]
        a = json.dumps(extract(items), sort_keys=True)
        b = json.dumps(extract(list(reversed(items))), sort_keys=True)
        self.assertEqual(a, b)

    def test_the_frozen_content_always_fits_the_storage_limit(self):
        items = [review_json(1000 + i, body="x " * 2000, state="APPROVED") for i in range(12)]
        self.assertLessEqual(len(json.dumps(extract(items), sort_keys=True)), 6000)


if __name__ == "__main__":
    unittest.main()
