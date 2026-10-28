# pgroll-migrations — zero-downtime schema change on a branch (S7)

[English](#english) | [한국어](#한국어)

## English

The CLI bundles [pgroll](https://github.com/xataio/pgroll) 0.16.3 as `xata roll`. `run.sh` branches
`base` to `lab-mig` and applies [`migrations/01_display_name_email_domain.yaml`](migrations/01_display_name_email_domain.yaml):

- rename `users.full_name` → `display_name`
- add `users.email_domain`, backfilled with `split_part(email, '@', 2)`

The migration file was generated with `xata roll convert` from two `ALTER TABLE` statements.
`convert` leaves `up: 'TODO: Implement SQL data migration'` for the new column, which is filled in by hand.

### Steps and checks

| Step | Command | Check |
|---|---|---|
| Init | `xata roll init` | — |
| Baseline | `xata roll baseline 00_baseline <dir>` | Needed: `start` on a non-empty schema without history fails with *Run `pgroll baseline` first* |
| Start | `xata roll start <file> --use-version-schema` | A version schema `public_01_display_name_email_domain` appears; 100,000 rows backfilled |
| Mid-migration | old client writes `public.users(full_name)`; new client writes `public_01_….users(display_name)` | Each sees the other's row; the old client's row gets `email_domain` derived |
| Complete | `xata roll complete` (no file argument) | `public.users` has only the new columns |
| Parent | — | `base.users` unchanged |

### Results

First run, 2026-10-28 (M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, pgroll 0.16.3): every
check passed. `start` with the backfill of 100,000 rows took 2.8–3.2 s and `complete` took
1.1–3.8 s. While the migration ran, `public.users` carried the helper columns
`_pgroll_new_email_domain` and `_pgroll_needs_backfill`.

**Clean run**, 2026-10-28: 5/5 checks passed; `start` with the backfill took 2.6 s and `complete` 1.3 s.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

CLI는 [pgroll](https://github.com/xataio/pgroll) 0.16.3을 `xata roll`로 포함하고 있습니다. `run.sh`는
`base`에서 `lab-mig` 브랜치를 만들고
[`migrations/01_display_name_email_domain.yaml`](migrations/01_display_name_email_domain.yaml)을 적용합니다.

- `users.full_name`을 `display_name`으로 rename
- `users.email_domain` 추가, `split_part(email, '@', 2)`로 backfill

마이그레이션 파일은 `ALTER TABLE` 두 문장으로 `xata roll convert`를 돌려 만들었습니다. `convert`는 새 컬럼의
`up`을 `'TODO: Implement SQL data migration'`으로 남기므로 그 부분은 손으로 채웠습니다.

### 단계와 확인

| 단계 | 명령 | 확인 |
|---|---|---|
| 초기화 | `xata roll init` | — |
| 기준선 | `xata roll baseline 00_baseline <dir>` | 필수. 기록 없이 비어 있지 않은 스키마에서 `start`하면 *Run `pgroll baseline` first*로 실패 |
| 시작 | `xata roll start <file> --use-version-schema` | 버전 스키마 `public_01_display_name_email_domain`이 생기고 10만 행이 backfill됨 |
| 진행 중 | 구 클라이언트는 `public.users(full_name)`에, 신 클라이언트는 `public_01_….users(display_name)`에 쓰기 | 서로의 행이 보이고, 구 클라이언트의 행에는 `email_domain`이 계산되어 들어감 |
| 완료 | `xata roll complete`(파일 인자 없음) | `public.users`에는 새 컬럼만 남음 |
| 부모 | — | `base.users`는 그대로 |

### 결과

첫 실행 2026-10-28(M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, pgroll 0.16.3)에서 모든 확인을
통과했습니다.
- 10만 행 backfill을 포함한 `start`는 2.8–3.2초, `complete`는 1.1–3.8초가 걸렸습니다.
- 마이그레이션이 진행되는 동안 `public.users`에는 보조 컬럼 `_pgroll_new_email_domain`과
  `_pgroll_needs_backfill`이 붙어 있었습니다.

**클린 실행**, 2026-10-28: 확인 5/5 통과. backfill을 포함한 `start`는 2.6초, `complete`는 1.3초였습니다.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
