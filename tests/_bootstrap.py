"""
Shared test bootstrap: wires up the offline genlayer SDK stub, loads the
contract once (contract.py by default, or the file named by the
RB_CONTRACT environment variable, which is how the stripped deploy file is
tested), replaces the contract's single clock with a controllable one, and
provides builders for jobs, GitHub pull request records and flows.
"""
import datetime as _dt
import hashlib as _hashlib
import importlib.util
import json as _json
import os
import sys
from unittest.mock import patch as _patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

CONTRACT_PATH = os.environ.get("RB_CONTRACT") or os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("peerlens_contract", CONTRACT_PATH)
_contract_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_module)

m = _contract_module
PeerLens = m.PeerLens
gl = m.gl
Address = m.Address
u256 = m.u256

# Production floors are 1e15 wei; tests use tiny amounts.
PeerLens.MIN_STAKE = u256(1)
PeerLens.MIN_BOND = u256(1)
PeerLens.MIN_JUROR_STAKE = u256(1)
PeerLens.MIN_APPEAL_BOND = u256(1)

REAL_NOW_UTC = PeerLens.__dict__["_now_utc"]

UTC = _dt.timezone.utc
BASE = _dt.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
DAY = 86400


class Clock:
    """The contract's only clock, replaced for tests."""

    def __init__(self):
        self.now = BASE

    def reset(self):
        self.now = BASE

    def set(self, moment):
        self.now = moment

    def advance(self, seconds):
        self.now = self.now + _dt.timedelta(seconds=seconds)


CLOCK = Clock()
PeerLens._now_utc = staticmethod(lambda: CLOCK.now)

PARTY_A_ADDRESS = "0x" + "11" * 20
PARTY_B_ADDRESS = "0x" + "22" * 20
STRANGER_ADDRESS = "0x" + "33" * 20
JUROR_ADDRESSES = ["0x" + hex(0xa0 + i)[2:].rjust(2, "0") * 20 for i in range(12)]

REPO = "acme/widgets"
PR_NUMBER = 7
MIN_CHARS = 100
LOGIN = "OctoDev"
REWARD = 10000
BOND_BPS = 2000
BOND = REWARD * BOND_BPS // 10000
PARTIAL_BPS = 5000


def make_contract():
    CLOCK.reset()
    reset_transfers()
    return PeerLens()


def set_caller(address_str):
    gl.message.sender_address = Address(address_str)


def reset_transfers():
    gl.evm.transfers.clear()


class PaymentRejected(gl.vm.UserError):
    """Raised by the test helper when a payable call was rejected and its
    GEN refunded to the sender (the contract does not revert in that case,
    so it can never keep GEN with no record of the sender)."""


def _state(contract):
    return (dict(contract.jobs), int(contract.job_count), dict(contract.jurors), dict(contract.review_claims),
            int(contract.settled_count), int(contract.cancelled_count))


def call_payable(contract, method_name, value, *args, **kwargs):
    """Invoke a payable method as if `value` wei were sent with the call.

    - The value is credited to the contract balance first; if the method
      raises (only possible with no GEN attached) the credit is rolled
      back, like a reverted transaction.
    - If the method returns a rejection, the helper checks that the exact
      value was credited back to the sender and that nothing else changed,
      then raises PaymentRejected so tests can assert the rejection."""
    gl.message.value = u256(value)
    contract.balance = contract.balance + u256(value)
    sender = str(gl.message.sender_address).lower()
    before_state = _state(contract)
    before_owed = int(contract.pending_withdrawals[sender]) if sender in contract.pending_withdrawals else 0
    try:
        result = getattr(contract, method_name)(*args, **kwargs)
    except Exception:
        contract.balance = contract.balance - u256(value)
        raise
    finally:
        gl.message.value = u256(0)
    if isinstance(result, str) and result.startswith('{"refunded_wei"'):
        data = _json.loads(result)
        after_owed = int(contract.pending_withdrawals[sender]) if sender in contract.pending_withdrawals else 0
        assert data["refunded_wei"] == str(int(value)), data
        assert after_owed - before_owed == int(value), (before_owed, after_owed, value)
        assert _state(contract) == before_state, "a rejected payable call changed state"
        raise PaymentRejected(data["rejected"])
    return result


def iso(moment):
    return moment.isoformat()


def gh_ts(epoch):
    """GitHub-style timestamp for epoch seconds."""
    return _dt.datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


BASE_EPOCH = int(BASE.timestamp())
DEFAULT_DEADLINE = BASE + _dt.timedelta(days=3)
DEADLINE_EPOCH = int(DEFAULT_DEADLINE.timestamp())


def pr_api_url(number=PR_NUMBER, repo=REPO):
    return f"https://api.github.com/repos/{repo}/pulls/{number}"


def reviews_url(number=PR_NUMBER, repo=REPO):
    return f"https://api.github.com/repos/{repo}/pulls/{number}/reviews?per_page=100"


LONG_BODY = (
    "I traced the sort comparator through the three call sites and found that the stable-sort assumption "
    "does not hold for equal keys. Please add a regression test for ties and document the ordering guarantee."
)


def review_json(rid=1001, **over):
    """A GitHub review record. By default: a COMMENTED review by the anchored
    reviewer on the anchored pull request, submitted one day after the job
    was posted, with a substantive body."""
    data = {
        "id": rid,
        "node_id": "PRR_x",
        "user": {"login": LOGIN},
        "body": LONG_BODY,
        "state": "COMMENTED",
        "submitted_at": gh_ts(BASE_EPOCH + DAY),
        "pull_request_url": pr_api_url(),
        "commit_id": "abc123",
        "author_association": "CONTRIBUTOR",
        "html_url": "https://github.com/acme/widgets/pull/7#pullrequestreview-1",
    }
    for key, value in over.items():
        if value is KeyError:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def other_review_json(rid=2001, login="maintainer", **over):
    """Somebody else's review of the same pull request."""
    return review_json(rid, user={"login": login}, **over)


def late_review_json(rid=1001, **over):
    """The reviewer's review submitted only after the deadline: the one
    review fact that only the reviewer controls, so it supports NOT_DELIVERED."""
    base = dict(submitted_at=gh_ts(DEADLINE_EPOCH + 3600))
    base.update(over)
    return review_json(rid, **base)


def dismissed_review_json(rid=1001, **over):
    base = dict(state="DISMISSED")
    base.update(over)
    return review_json(rid, **base)


def short_review_json(rid=1001, **over):
    base = dict(body="LGTM")
    base.update(over)
    return review_json(rid, **base)


def pages_of(*reviews, number=PR_NUMBER):
    """One evidence page: the review list of the anchored pull request."""
    return {reviews_url(number): list(reviews)}


def create_job(c, reward=REWARD, deadline=None, party_a=PARTY_A_ADDRESS, party_b=PARTY_B_ADDRESS,
                  repo=REPO, pr=PR_NUMBER, login=LOGIN, min_chars=MIN_CHARS, terms="Review the sorting change",
                  criteria="Checks the tie-handling and asks for tests", bond_bps=BOND_BPS, partial_bps=PARTIAL_BPS):
    set_caller(party_a)
    return call_payable(
        c, "create_job", reward, party_b, repo, pr, login, min_chars, terms, criteria,
        iso(deadline or DEFAULT_DEADLINE), bond_bps, partial_bps,
    )


def accept_job(c, job_id, bond=BOND, party_b=PARTY_B_ADDRESS):
    set_caller(party_b)
    return call_payable(c, "accept_job", bond, job_id)


def get(c, job_id):
    return _json.loads(c.get_job(job_id))


def submit_and_lock(c, job_id, urls, party=PARTY_B_ADDRESS):
    """Submit evidence as `party`; if the counterparty has not answered,
    move past the grace period and lock, as a real caller would."""
    set_caller(party)
    c.submit_evidence(job_id, urls)
    if get(c, job_id)["status"] == "active":
        CLOCK.set(_dt.datetime.fromisoformat(get(c, job_id)["evidence_lock_at"]) + _dt.timedelta(seconds=1))
        c.lock_evidence(job_id)


def pass_deadline(c, job_id):
    b = get(c, job_id)
    deadline = _dt.datetime.fromisoformat(b["deadline"])
    if CLOCK.now <= deadline:
        CLOCK.set(deadline + _dt.timedelta(seconds=5))


def web_for(pages):
    """A render replacement: url -> text (dict values are JSON-encoded)."""
    def render(url, mode="text"):
        value = pages.get(url)
        if value is None:
            raise RuntimeError("404")
        return value if isinstance(value, str) else _json.dumps(value)
    return render


MODEL_DELIVERED = {"outcome": "REVIEW_DELIVERED", "reasoning": "criteria met"}
MODEL_PARTIAL = {"outcome": "PARTIALLY_DELIVERED", "reasoning": "substantive work"}
MODEL_UNDETERMINED = {"outcome": "UNDETERMINED", "reasoning": "thin evidence"}


def adjudicate(c, job_id, pages, model=None, prompts=None, caller=STRANGER_ADDRESS):
    """Run adjudicate with the given fetch results and model answer.
    `model` is a dict, an Exception instance to raise, or None (the model
    must then not be called). Returns the job dict."""
    calls = prompts if prompts is not None else []

    def exec_prompt(prompt, response_format="json"):
        calls.append(prompt)
        if model is None:
            raise AssertionError("the model must not be called here")
        if isinstance(model, Exception):
            raise model
        return model

    set_caller(caller)
    with _patch.object(gl.nondet.web, "render", side_effect=web_for(pages)), \
            _patch.object(gl.nondet, "exec_prompt", side_effect=exec_prompt):
        c.adjudicate(job_id)
    return get(c, job_id)


def active_job(c, **over):
    """A job posted and accepted at the base time."""
    job_id = create_job(c, **over)
    reward = over.get("reward", REWARD)
    bond_bps = over.get("bond_bps", BOND_BPS)
    accept_job(c, job_id, bond=reward * bond_bps // 10000)
    return job_id


def locked_job(c, urls=None, both=False, **over):
    """An accepted job whose evidence is locked. With `both` the sponsor
    answers too, so the lock happens at once and the clock does not move;
    otherwise the clock moves past the counterparty grace period."""
    urls = urls or [reviews_url()]
    job_id = active_job(c, **over)
    if both:
        set_caller(PARTY_B_ADDRESS)
        c.submit_evidence(job_id, urls)
        set_caller(PARTY_A_ADDRESS)
        c.submit_evidence(job_id, urls)
    else:
        submit_and_lock(c, job_id, urls)
    return job_id


def ruled_job(c, pages=None, model=None, urls=None, after_deadline=True, **over):
    """A job carried through Tier 1."""
    pages = pages if pages is not None else pages_of(review_json())
    job_id = locked_job(c, urls=urls or list(pages.keys()), **over)
    if after_deadline:
        pass_deadline(c, job_id)
    adjudicate(c, job_id, {u: v for u, v in pages.items()}, model=model)
    return job_id


def register_jurors(c, count=6, base_stake=1000):
    for i, addr in enumerate(JUROR_ADDRESSES[:count]):
        set_caller(addr)
        call_payable(c, "register_juror", base_stake + i * 100)


def pass_appeal_window(c, job_id):
    b = get(c, job_id)
    CLOCK.set(_dt.datetime.fromisoformat(b["appeal_deadline"]) + _dt.timedelta(seconds=120))


def draw_jury(c, job_id, beacon="fixed-test-beacon"):
    pass_appeal_window(c, job_id)
    b = get(c, job_id)
    page = _json.dumps({"round": b["appeal"]["draw_round"], "randomness": beacon})
    with _patch.object(gl.nondet.web, "render", return_value=page):
        c.draw_jury(job_id)


def commit_hash(vote, salt, job_id, juror):
    return _hashlib.sha256(f"{vote}:{salt}:{str(juror).lower()}:{job_id}".encode()).hexdigest()


def pending(c, address):
    return int(c.get_pending_withdrawal(address))


def expect_user_error(testcase, fn, *args, **kwargs):
    with testcase.assertRaises(gl.vm.UserError) as ctx:
        fn(*args, **kwargs)
    return str(ctx.exception)


def anchors(**over):
    """The anchor fields the module-level helpers read, as a plain dict."""
    b = {
        "repo": REPO, "repo_key": REPO.lower(), "pr_number": PR_NUMBER,
        "reviewer_login": LOGIN, "reviewer_key": LOGIN.lower(), "min_review_chars": MIN_CHARS,
        "created_epoch": BASE_EPOCH, "deadline_epoch": DEADLINE_EPOCH,
        "job_terms": "Review the sorting change", "review_criteria": "Checks the tie-handling and asks for tests",
        "created_at": iso(BASE), "deadline": iso(DEFAULT_DEADLINE),
    }
    b.update(over)
    return b


def gather(mapping, b=None):
    """Run the contract's page gathering over a url -> content mapping."""
    with _patch.object(gl.nondet.web, "render", side_effect=web_for(mapping)):
        return m._gather_pages(list(mapping.keys()), b or anchors())


def assess(mapping, b=None):
    b = b or anchors()
    return m._assess(gather(mapping, b), b)
