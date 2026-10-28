# STATUS.md

**As of 2026-10-28** — repository created. Ten scenarios (S1–S10) run on a local kind cluster;
the first run and a clean re-run from `oss/platform/up.sh` are recorded in each lab's README and below. `run-all.sh` changed after the clean run only in how it prints script paths in its log.

## CI

`checks` runs one job on pull requests, `guard`: gitleaks and the repository-hygiene checks. `links`
and `syntax` run only by hand (`gh workflow run checks.yml --ref <branch>`). Nothing runs on push to
`main`; the pre-commit hook covers direct pushes. The labs themselves need a 16 GiB Docker and about
40 minutes, so they run locally only.

## Inventory

7 labs in the README table (S1–S10); 0 single-language. All 7 publish notes-site cards (`web: true`, category `xata`, owner's choice 2026-10-28); `docs/labs.json` lists 7.

## Pins

Change a row only together with a re-run.

| Pin | Version | Where |
|---|---|---|
| xataio/xata | `f197e7bad539a81b424018765ee4e8799ed184f0` (main, 2026-10-23) | `oss/platform/up.sh` `XATA_REF` |
| Xata CLI | 1.18.0 (pgroll 0.16.3, pgstream 1.5.0) | installed by `xata.io/install.sh`; not pinned by the script — record the version in each run |
| Branch image | `postgres:18.6` → `ghcr.io/xataio/postgres-images/cnpg-postgres-plus:18.6` | `up.sh` `PG_VERSION` |
| Source container | `postgres:18.6@sha256:979bccfd…` | `oss/anonymized-clone/run.sh` |
| kind | v0.33.0 (Kubernetes v1.37.0) | local install |

## Runs

| Date | What | Result |
|---|---|---|
| 2026-10-28 | First run, scenario by scenario, while the scripts were written | S1–S10 passed. Four findings (root README) |
| 2026-10-28 15:37–16:25 UTC | Clean run: `down.sh`, `up.sh` (16 min 31 s), `run-all.sh` (32 min 0 s) — [log](oss/results/run-all-20261028T155333Z.txt) | ALL LABS PASSED. One N=6 fan-out run stalled for about 15 min on node memory (5/6); a repeat at 16:28 gave 6/6 in 116 s (`oss/agent-fanout/README.md`) |

## Open work

Tracked as issues — [all open](https://github.com/litkhai/xata-hols/issues).
