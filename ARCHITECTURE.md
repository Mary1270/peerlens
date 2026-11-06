# PeerLens - Architecture

## 1. Design goals

1. Decide every structured fact in code, never by a model.
2. Use the model only for questions code cannot answer, and never let it override a code-proven fact.
3. Make every verdict path depend on content actually fetched and frozen on-chain.
4. Give every stuck state a permissionless way out.
5. Keep payouts pull-based so no settlement path can be blocked by a recipient.

## 2. Two-tier adjudication

**Tier 1 (`adjudicate`).** Anyone can call it once evidence is locked. The leader fetches the locked GitHub review list, the contract parses it, and `_assess` classifies every review by the anchored reviewer:

| Verdict | Meaning | Effect |
| --- | --- | --- |
| delivered | submitted inside [creation, deadline]; state APPROVED, CHANGES_REQUESTED or COMMENTED; at least `min_review_chars` characters | DELIVERED only if the merit check affirms the criteria; PARTIAL if it affirms part; otherwise UNDETERMINED |
| ambiguous | submitted in the window, not dismissed, but too short or in an unrecognised state | UNDETERMINED; blocks NOT_DELIVERED |
| rejected | submitted in the window and later dismissed | UNDETERMINED; a maintainer can cause it |
| failed | submitted after the deadline | supports NOT_DELIVERED |
| unrelated | still pending, no readable timestamp, or submitted before the job existed | ignored |

Reviews by anyone else, and reviews whose `pull_request_url` is not the anchored pull request, are dropped before classification.

NOT_DELIVERED needs code-supported non-delivery (a failed review, or a complete review list with no counting review from the reviewer, ruled at least one hour after the deadline) and the model's agreement. Anything else is UNDETERMINED.

**No payment without a merit answer.** The model is consulted for every delivered review. Silence of the model never pays: if it gives no usable answer, `adjudicate` reverts without recording anything and can be retried; after the model window it records UNDETERMINED (each side refunded) and the jury stays open. A REVIEW_DELIVERED claim is accepted only for a review that passed every structured check.

Validators re-fetch and re-run the same code and compare the outcome, whether the model was used, the full assessment and a manifest of decisive fields. They never compare raw page hashes. Full-content hashes are audit-only, and an evidence root commits to the manifest plus those hashes.

**Model window.** The model is consulted only until 3 days after the deadline. If validators keep disagreeing on a model answer, a later call rules in code only: nothing is paid on unjudged work, NOT_DELIVERED becomes impossible, and a jury may still decide within the permitted options.

**Tier 2 (appeal).** Either party may appeal within the window by posting a bond, but only if the frozen evidence permits a different outcome (`get_jury_options`). The juror pool is snapshotted at appeal time and the jury is drawn from a drand round that did not exist yet, using integer-only weighted sampling anyone can replay (`verify_jury_selection`). Jurors commit a hash bound to their address and the job, then reveal. A strict majority of the selected jurors stands only if it is one of the permitted outcomes, checked in code on every validator against the frozen evidence:

| Frozen evidence shows | Jury may choose |
| --- | --- |
| a delivered review | DELIVERED, PARTIAL, UNDETERMINED |
| a dismissed review | NOT_DELIVERED, PARTIAL, UNDETERMINED |
| an in-time but too short review | PARTIAL, UNDETERMINED |
| code-supported non-delivery | NOT_DELIVERED, UNDETERMINED |
| nothing decisive | UNDETERMINED |

So the jury can correct Tier 1, but can never award DELIVERED without a review that passed every structured check.

**Jury pay is neutral.** With a verified majority, half the appeal bond is the jury fee and goes to the majority jurors whichever way they voted. The other half goes back to the appellant if the outcome changes, or to the other party if it stands. Non-revealers and minority voters are slashed; the slashed stake also goes to the majority.

## 3. Evidence: one record, built from the anchors

The accepted evidence is a single URL, rebuilt from the on-chain repository and pull request number: the review list of that pull request with `per_page=100`. Every spelling of it (with or without the query, any case in the owner and name) collapses to the canonical URL. Other hosts, ports, userinfo, fragments, other queries, other pull requests, comment lists and single-review URLs are rejected.

The extractor keeps only the anchored reviewer's reviews, each reduced to id, state, `submitted_at` (strict format), cleaned body length and a 600-character excerpt. At most six reviews are tracked (substantive in-window ones first), so the frozen content always fits the storage limit. The list is "complete" only if it holds fewer than 100 entries; a full page cannot prove absence.

## 4. Maintainer actions

A maintainer can dismiss a review. A dismissed review is "rejected": Tier 1 rules UNDETERMINED (each side refunded) and either party may ask the jury, which may then award the partial split or non-delivery. Only facts the reviewer alone controls support breach: no review at all in a complete list, or a review submitted after the deadline.

## 5. Claim registry

`review_claims` maps `<repo>#<pr>/<review id>` to a job id. A delivered or partial outcome claims the review. Priority between jobs that name the same review follows the contract's own creation counter (`job_<n>`), never a timestamp: an unsettled claim held by a later job is taken over by an earlier one, and the displaced job is settled as UNDETERMINED (each side refunded). A settled claim is final and no payout is ever reversed. The claim is released if the final outcome is not a claim outcome.

## 6. Money

- Reward is escrowed by the sponsor, bond (reward x bond_bps / 10000, 5% to 100%) by the reviewer.
- Payouts are credited to `pending_withdrawals` and moved only by `withdraw`, which zeroes the ledger before the transfer.
- Rounding on a partial split goes to the sponsor, and totals are conserved for every outcome.
- Appeal bond: 20% of the reward; half is the jury fee.
- A payable call rejected while GEN is attached credits the GEN back to the sender instead of reverting.

## 7. Recovery paths

| Stuck state | Way out |
| --- | --- |
| Posted, never accepted | `cancel_job` (sponsor) |
| Active, no evidence | `expire_no_evidence` |
| Active, one side silent | `lock_evidence` after the grace period |
| Evidence locked, validators disagree on the model | code-only `adjudicate` after the 3-day model window |
| Evidence locked, nobody rules | `expire_unruled` after the timeout |
| Ruled, no appeal | `settle_unappealed` |
| Appeal filed, never drawn | `recover_stuck_appeal` |
| Jury voting, never settled | `recover_stuck_appeal` |

## 8. Platform rules this contract follows

- Deploy file is two header lines then code, generated with `ast` and `tokenize` (never regex) and verified by comparing syntax trees.
- Storage types are class-level annotations; nothing assigns `{}` or `[]` to a storage container in `__init__`.
- `run_nondet_unsafe` takes leader and validator positionally, both defined without capturing `self`.
- Errors such as a 404 are caught inside the nondeterministic closure and returned as plain values.
- Caller identity comes from `gl.message.sender_address`; time comes from a single clock function that reads the transaction timestamp and falls back to `datetime.now()`.
- `exec_prompt(response_format="json")` is read as an object, never `.strip()`ed.
- GEN attached to a rejected payable call is credited back, not left in the contract.

## 9. Known limitations (disclosed)

1. **Only the delivered path has been run live.** The short-window build ran end to end on GenLayer Studio and the review-list array from `api.github.com` is read and parsed correctly through `gl.nondet.web.render`. The not-delivered, undetermined, appeal and jury branches are covered offline only. `TESTNET_TESTS.md` records the run.
2. **A review can be edited after it is submitted.** GitHub lets a reviewer change the text of a submitted review, and `submitted_at` does not move. A reviewer could submit a thin review in time and expand it before `adjudicate` runs. The lock fixes which record is judged, not its content, so the sponsor should lock and adjudicate promptly; a proven review can be ruled before the deadline, and the frozen content is what any jury sees.
3. **No wallet-to-GitHub binding.** Nothing proves a wallet controls a GitHub login. A job created first for the same repository, pull request and login has claim priority. The genuine job is refunded, not lost, but the reviewer is not paid.
4. **Pagination.** Only the first 100 reviews of a pull request are read. A full page cannot prove absence, so the case waits for a retry or is refunded by `expire_unruled`.
5. **Unauthenticated GitHub API.** Rate limits make a fetch fail, which delays resolution rather than producing a wrong verdict.
6. **Merit is judged by a model over an excerpt.** Code proves structure (author, pull request, time, state, length). Whether the review satisfies the sponsor's criteria is a model judgment over the state, length and the first 600 characters, not an inspection of the code under review. The model can never create a delivery the code did not prove.
7. **Length is a floor, not a quality bar.** Padding with words passes the length check and is then judged by the model; a well-worded but shallow review could pass. The remedies are the sponsor's appeal and the criteria they write.
8. **Reviews before the job never count.** The job pays for work done after it was posted.
9. **A GitHub username change breaks the reviewer match.** The login is anchored at creation.
10. **Only the review list is read.** Inline review comments are not fetched, so a review whose substance is all in inline comments needs a body that says so.
11. **Stake model.** Juror weight is capped and reputation-scaled, but several wallets controlled by one person are not detected.
12. **Jury threshold.** A strict majority of the selected jurors is needed. Low participation means the Tier-1 outcome stands and the appeal bond is returned.
13. **drand and clock.** The jury draw trusts the public drand beacon for that round and relies on the validators' pinned transaction time.
14. **Single evidence host.** All evidence comes from api.github.com.
15. **An appeal delays settlement by about five days** (draw after the window, two days to commit, one to reveal).

## 10. Testing

Offline tests run against a self-written SDK stub that reproduces the real SDK's habits: empty storage containers, range-checked `u256`, a JSON round trip of the leader result, and a validator that receives `gl.vm.Return`. A controllable clock replaces the contract's single clock function. The whole suite runs against both `contract.py` and `contract_deploy.py`. The stub does not simulate real networks, real models or multi-validator consensus. `contract_testnet_deploy.py` differs only in its time constants, so a handful of tests that assert production windows fail against it by design; it is not part of CI.
