# PeerLens

Escrow and dispute resolution for paid code reviews on GitHub, built as a GenLayer Intelligent Contract.

A sponsor escrows a reward for a review of one pull request. A reviewer posts a performance bond. When the deadline arrives, the contract reads the pull request's review list itself from the GitHub API and decides in code whether a real review was delivered. A model is consulted only for questions code cannot answer: whether the text of the review actually meets the sponsor's criteria. A staked, commit-reveal jury can overturn the automated outcome, but never without independent verification against the frozen evidence.

The central rule: **a review that a maintainer dismisses is not a reviewer who failed.** A review submitted in time by the right person and later dismissed is never resolved as not delivered. It resolves as undetermined with both deposits refunded and the jury still available. Only things the reviewer alone controls (no review at all, or a review submitted after the deadline) can support a not-delivered ruling.

## What is anchored on-chain at creation

| Anchor | Example |
| --- | --- |
| Repository | `owner/name` |
| Pull request number | `42` |
| Reviewer GitHub login | `octodev` |
| Minimum review length | `200` characters |
| Deadline | ISO timestamp |
| Evidence host | `api.github.com` only |
| Bond and partial split | basis points |

The reviewer sees every anchor before posting the bond.

## How a delivery is proven

The only accepted evidence is `https://api.github.com/repos/<repo>/pulls/<n>/reviews?per_page=100`, rebuilt from the anchors. The contract keeps only the anchored reviewer's reviews of that pull request. A review proves delivery only if all of these hold:

1. `user.login` equals the reviewer login (case-insensitive)
2. `pull_request_url` is the anchored pull request
3. `submitted_at` falls inside the window between creation and deadline (integer time math)
4. `state` is `APPROVED`, `CHANGES_REQUESTED` or `COMMENTED` (not `DISMISSED`, not `PENDING`)
5. The text has at least the anchored number of characters (whitespace is collapsed first, so padding does not count)

A code backstop forces any "delivered" claim down to undetermined unless every check passed. Passing the checks is necessary, not sufficient: full payment also needs an affirmative merit answer, and the silence or failure of the model never authorises payment. One review can settle only one job.

## Outcomes and payouts

| Outcome | Reviewer receives | Sponsor receives |
| --- | --- | --- |
| REVIEW_DELIVERED | reward + bond | nothing |
| PARTIALLY_DELIVERED | bond + reward x partial share | rest of the reward |
| NOT_DELIVERED | nothing | reward + bond |
| UNDETERMINED | bond | reward |

Not delivered needs both proof from the evidence (a complete review list with no counting review from the reviewer, ruled at least one hour after the deadline, or a review submitted after the deadline) and agreement from the model. A review a maintainer dismissed, or a review that is too short, is never automatic breach: Tier 1 refunds both sides and either party may ask the jury.

## Rules worth knowing

- **Early or immature rulings are rejected, not recorded.** If the review list cannot be read, is truncated at 100 entries, or is empty less than one hour after the deadline, `adjudicate` reverts and the case stays open for a later attempt.
- **Rejected payments are refunded, not stranded.** A payable call that is rejected while GEN is attached credits that GEN to the sender's withdrawable balance and returns the reason.
- **The jury is bounded by the evidence.** `get_jury_options` lists what a jury may decide; it can never award delivery without a fully checked review, and jurors earn the same fee whichever way they vote.
- **Unjudged work is never paid.** If the model gives no usable answer, `adjudicate` reverts and can be retried. After 3 days past the deadline it rules in code only: delivered reviews become undetermined (each side refunded) and the jury can still decide.
- **Padding earns nothing.** Whitespace does not count toward the minimum length, and a review that passes the length check still has to pass the merit check against the sponsor's criteria.
- **One review, one reward.** When several jobs name the same review, the earliest created (by the contract's own counter) wins while the others are unsettled; the rest are refunded.

## Lifecycle

`posted` -> `active` -> `evidence_locked` -> `ruled` -> (`appeal_filed` -> `jury_voting`) -> `settled`

Every state has a way out: cancel, expire without evidence, expire unruled, settle unappealed, recover a stuck appeal. All payouts are credited to a ledger and moved only by `withdraw`.

## Repository layout

| Path | Purpose |
| --- | --- |
| `contract.py` | Documented contract |
| `contract_deploy.py` | Generated file to paste into GenLayer Studio |
| `contract_testnet_deploy.py` | Same contract with short time windows, for live testing |
| `TESTNET_TESTS.md` | Live test plan and record |
| `tools/make_deploy.py` | Builds the deploy file using `ast` and `tokenize` |
| `tools/make_testnet_deploy.py` | Builds the short-window test copy from the deploy file |
| `index.html` | Single-file frontend (genlayer-js from esm.sh) |
| `tests/` | Offline tests with a self-written SDK stub |
| `ARCHITECTURE.md` | Design and disclosed limitations |

## Run the tests

No installs are needed.

```
python3 -m unittest discover -s tests
RB_CONTRACT=contract_deploy.py python3 -m unittest discover -s tests
python3 tools/make_deploy.py --check
```

## Deploy

1. Run `python3 tools/make_deploy.py` after any change to `contract.py`.
2. In GenLayer Studio, create a new contract file and paste the full content of `contract_deploy.py`. The file begins with exactly two header lines and contains no comments or docstrings, which Studio requires.
3. Deploy with no constructor arguments.
4. Open `index.html?contract=<deployed address>` once. The address is remembered in the browser. To bake the address in, edit `DEFAULT_CONTRACT_ADDRESS` in `index.html`.

## Methods

Writes: `create_job`, `accept_job`, `cancel_job`, `submit_evidence`, `lock_evidence`, `adjudicate`, `settle_unappealed`, `expire_no_evidence`, `expire_unruled`, `recover_stuck_appeal`, `register_juror`, `unstake_juror`, `file_appeal`, `draw_jury`, `commit_vote`, `reveal_vote`, `settle_jury`, `withdraw`.

Views: `get_job`, `list_jobs`, `get_stats`, `total_jobs`, `get_review_claim`, `get_jury_options`, `count_eligible_jurors`, `get_juror`, `get_pending_withdrawal`, `get_contract_balance`, `verify_jury_selection`.

## Status

Offline tests (see `tests/`) cover the state machine, review extraction and assessment, the evidence URL rules, payouts and the jury, including adversarial regression tests in `tests/test_steward_corrections.py`: unavailable or negative model answers never authorise full payment, and unverified, padded, late or foreign reviews never earn a payout automatically.

The short-window build (`contract_testnet_deploy.py`) was run live on GenLayer Studio at `0xa8671E2Dd98E25376DD503692685e525Dd2291B3`. One job went through the full path: create, accept, evidence from both parties, automated ruling `REVIEW_DELIVERED`, settlement after the appeal window and withdrawal, ending with a contract balance of 0 GEN. A review posted before the job existed was correctly classified as unrelated. The not-delivered and undetermined branches have not been run live yet. See `TESTNET_TESTS.md` for the record and `ARCHITECTURE.md` for known limitations.

## License

MIT

## CI

The offline test suite runs on every push; see the Actions tab.
