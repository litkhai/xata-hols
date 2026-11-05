# STATUS.md

**As of 2026-11-05** — eleven scenarios (S1–S11) run on a local kind cluster. S1–S10: the first run and
a clean re-run from `oss/platform/up.sh` are recorded in each lab's README and below; `run-all.sh` changed after the clean run only in how it prints script paths in its log.
S11 (`oss/agent-memory-redis`, #10) was run on its own after a fresh `up.sh`; it is not part of `run-all.sh`.

## CI

`checks` runs one job on pull requests, `guard`: gitleaks and the repository-hygiene checks. `links`
and `syntax` run only by hand (`gh workflow run checks.yml --ref <branch>`). Nothing runs on push to
`main`; the pre-commit hook covers direct pushes. The labs themselves need a 16 GiB Docker and about
40 minutes, so they run locally only.

## Inventory

8 labs in the README table (S1–S11); 0 single-language. The first 7 publish notes-site cards (`web: true`, category `xata`, owner's choice 2026-10-28); `docs/labs.json` lists 7. `oss/agent-memory-redis` has no `web` key yet (owner's choice pending).

## Pins

Change a row only together with a re-run.

| Pin | Version | Where |
|---|---|---|
| xataio/xata | `f197e7bad539a81b424018765ee4e8799ed184f0` (main, 2026-10-23) | `oss/platform/up.sh` `XATA_REF` |
| Xata CLI | 1.18.0 (pgroll 0.16.3, pgstream 1.5.0) | installed by `xata.io/install.sh`; not pinned by the script — record the version in each run |
| Branch image | `postgres:18.6` → `ghcr.io/xataio/postgres-images/cnpg-postgres-plus:18.6` | `up.sh` `PG_VERSION` |
| Source container | `postgres:18.6@sha256:979bccfd…` | `oss/anonymized-clone/run.sh` |
| kind | v0.33.0 (Kubernetes v1.37.0) | local install |
| Redis | `redis:8.10.3@sha256:de1068af…` (Redis Open Source 8.10.3, RediSearch 8.10.1) | `oss/agent-memory-redis/manifests/redis.yaml` |
| Bench client | `python:3.12-slim@sha256:b18b261e…` (Python 3.12.16); langgraph 1.2.20, langgraph-checkpoint-postgres 3.2.0, langgraph-checkpoint-redis 0.6.0, psycopg 3.3.8, redis-py 8.1.0 | `oss/agent-memory-redis/bench/Dockerfile`, `requirements.txt` |

## Runs

| Date | What | Result |
|---|---|---|
| 2026-10-28 | First run, scenario by scenario, while the scripts were written | S1–S10 passed. Four findings (root README) |
| 2026-10-28 15:37–16:25 UTC | Clean run: `down.sh`, `up.sh` (16 min 31 s), `run-all.sh` (32 min 0 s) — [log](oss/results/run-all-20261028T155333Z.txt) | ALL LABS PASSED. One N=6 fan-out run stalled for about 15 min on node memory (5/6); a repeat at 16:28 gave 6/6 in 116 s (`oss/agent-fanout/README.md`) |
| 2026-10-30 | `oss/branching/cow-probe.sh` (Docker only, no cluster) — [output](oss/branching/results/cow-probe-2026-10-30.txt) | No dm-thin / dm-snapshot on kernel 7.0.14-linuxkit, so no LVM CoW driver. 1.4 GB: `tar` round trip 30.4–30.6 s, `cp --reflink` 0.03 s (btrfs, XFS) |
| 2026-11-05 07:47–09:23 UTC | Fresh `up.sh` (kind cluster created, tilt ready in 346 s), then `oss/agent-memory-redis/run.sh` step by step — [results](oss/agent-memory-redis/results/) | 30 session runs, 4 kill, 6 expiry, 6 + 3 small-state, sizes: all completed. Two new findings (root README 5, 6). Runs replaced after fixes are kept in `results/superseded/` with the reason in their file names |

## Open work

**Correction 2026-10-30:** the claim that CLI 1.18.0 does not retry a project-busy error was wrong. Its API client retries a 429 with `Retry-After` up to 3 attempts, about 2 s (#5). The measurements are unchanged.

**Upstream 2026-10-30:** Findings 1 and 2 reported as [xataio/xata#16](https://github.com/xataio/xata/issues/16) (branch NetworkPolicy egress) and [xataio/xata#17](https://github.com/xataio/xata/issues/17) (project lock, short CLI retry). Both open, no reply yet.

Tracked as issues — [all open](https://github.com/litkhai/xata-hols/issues).
