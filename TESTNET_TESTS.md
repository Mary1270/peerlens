# Live test plan and record

Status: steps 1 to 8 run live on GenLayer Studio. Steps 9 to 12 are not run yet.

Test contract: `0xa8671E2Dd98E25376DD503692685e525Dd2291B3` (short-window build).

The short-window build `contract_testnet_deploy.py` differs from `contract_deploy.py` only in its time constants.

## Time constants

| Constant | Production | Test build |
| --- | --- | --- |
| Absence lag after deadline | 1 hour | 5 minutes |
| Model window after deadline | 3 days | 1 hour |
| Minimum deadline lead | 1 hour | 5 minutes |
| Evidence window | 3 days | 1 hour |
| Evidence grace | 24 hours | 5 minutes |
| Appeal window | 2 days | 5 minutes |
| Jury commit window | 2 days | 15 minutes |
| Jury reveal window | 1 day | 15 minutes |
| Resolution timeout | 7 days | 1 hour |

## Wallets

| Role | Address |
| --- | --- |
| Sponsor (party A) | `0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd` |
| Reviewer (party B) | `0x72BE82aD9d8800d45e5cc22b4f2C29BBeDF73bc6` |

## GitHub setup

| Item | Value |
| --- | --- |
| Test repository | any public repository you control |
| Pull request | open one pull request, note its number |
| Reviewer account | a second GitHub account that is not the pull request author; it leaves a COMMENTED review of at least 200 characters |

## Run sheet

| Step | Wallet | Call | Arguments | Value (GEN) | Expected | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Sponsor | `create_job` | reviewer wallet, repo, PR number, reviewer login, 200, terms, criteria, deadline (about 10 minutes ahead), 2000, 5000 | 10 | `job_0` | job_0 created, 10 GEN escrowed |
| 2 | Reviewer | `accept_job` | `job_0` | 2 | status active | accepted, 2 GEN bond |
| 3 | Reviewer account on GitHub | leave a COMMENTED review, 200+ characters | | | review visible in the API list | review 5804106575 posted before the job existed (see step 6 note) |
| 4 | Reviewer | `submit_evidence` | `job_0`, `["https://api.github.com/repos/<repo>/pulls/<n>/reviews?per_page=100"]` | 0 | stored in canonical form | stored in canonical form |
| 5 | Sponsor | `submit_evidence` | same URL | 0 | status evidence_locked | status evidence_locked |
| 6 | Sponsor | `adjudicate` | `job_0` | 0 | REVIEW_DELIVERED or PARTIALLY_DELIVERED, review claimed | first run reverted (review predates the job, classed unrelated, outcome undetermined). After a second review (5804309853) in the window: REVIEW_DELIVERED, review claimed, status ruled |
| 7 | Sponsor | `settle_unappealed` | `job_0` after the appeal window | 0 | status settled | status settled |
| 8 | Reviewer | `withdraw` | | 0 | reviewer receives reward + bond (full delivery) | reviewer paid, contract balance 0 GEN |
| 9 | Sponsor | `create_job` | second job, reviewer login with no review | 10 | `job_1` | |
| 10 | Sponsor | `adjudicate` | `job_1` after deadline + 5 minutes | 0 | NOT_DELIVERED | |
| 11 | Sponsor | `create_job` | third job, review shorter than the minimum | 10 | `job_2` | |
| 12 | Sponsor | `adjudicate` | `job_2` after deadline | 0 | UNDETERMINED, model not asked | |

Record transaction hashes in this file after the run, and the final contract balance (expected 0 GEN after all withdrawals).


## Live findings

- The review list endpoint is readable by `gl.nondet.web.render` and parsed correctly by the extractor (ids, states, lengths, timestamps).
- A review submitted before the job was created is ignored (`in_window: false`), as designed; a new review in the window delivered.
- The reviewer and the pull request author were the same GitHub account in this run.
