# anonymized-clone — copy a Postgres with PII replaced (S10)

[English](#english) | [한국어](#한국어)

## English

`xata clone start` copies an external Postgres into a branch with
[pgstream](https://github.com/xataio/pgstream) 1.5.0 (bundled with the CLI), transforming columns on
the way in. `run.sh` uses a plain `postgres:18.6` container on `127.0.0.1:5433` as the "external"
database, loaded with the same [`seed-app.sql`](../platform/seed-app.sql) as `base`, and clones it into
a new root branch `lab-anon` with the rules in [`clone.yaml`](clone.yaml):

| Column | Transformer | Parameters |
|---|---|---|
| `users.email` | `neosync_email` | `preserve_domain: true` |
| `users.full_name` | `neosync_fullname` | — |
| `users.phone` | `phone_number` | `prefix: "010-"`, length 13, deterministic |

Transformer names and parameters: pgstream `docs/transformers.md` at `0a58e63`.

### Things the CLI help does not say

- `xata clone config --mode auto` wrote **no rules** for this schema (`table_transformers: []`).
  Detecting personal columns needs `--mode ai`, which sends the schema to an LLM
  (`--model gemini-2.5-flash` by default). The rules here are written by hand instead.
- `clone start` reads the rules from `.xata/clone.yaml` in the working directory. `--config` takes
  a whole pgstream config: passing `clone.yaml` there fails with
  *unsupported postgres source mode, must be one of 'replication', 'snapshot' or 'snapshot_and_replication'*.
- `config` fails with `ENOENT … .xata/clone.yaml` when `.xata/` does not exist.

### Checks and results

First run, 2026-10-28 (M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, pgstream 1.5.0): the clone
of 100,000 users and 300,000 orders took 3.6–3.8 s, and every check passed.

| Check | Result |
|---|---|
| Row counts equal the source | 100,000 / 300,000 |
| Emails, names or phones equal to the seed formula | 0 / 0 / 0. Positive control on the un-anonymized `base`: 100,000 each |
| Email domain kept; emails still unique | yes; 100,000 distinct |
| `orders.amount` sum and foreign keys | equal to source; 0 orphans |
| Example, user 42 | `user42@example.com \| Park Hayun \| 010-0042-0294` → `e755e10c…@example.com \| Siu Kuilder \| 010-986763646` |

pgstream logged three warnings:
- `neosync_email` does not guarantee unique output, although the column has a unique index.
- During restore, `DROP SCHEMA public` failed because `pg_stat_statements` depends on it.
- During restore, `CREATE SCHEMA public` failed because the schema already exists.

The last two were ignored and did not affect the result.

**Clean run**, 2026-10-28: 9/9 checks passed; the clone took 3.2 s with the same three warnings.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

`xata clone start`는 CLI에 포함된 [pgstream](https://github.com/xataio/pgstream) 1.5.0으로 외부 Postgres를
브랜치에 복사하면서 컬럼을 변환합니다. `run.sh`의 구성은 다음과 같습니다.
- **원본:** `127.0.0.1:5433`의 일반 `postgres:18.6` 컨테이너를 "외부" DB로 씁니다. `base`와 같은
  [`seed-app.sql`](../platform/seed-app.sql)을 넣습니다.
- **대상:** 새 루트 브랜치 `lab-anon`에 [`clone.yaml`](clone.yaml) 규칙으로 clone합니다.

| 컬럼 | 변환기 | 파라미터 |
|---|---|---|
| `users.email` | `neosync_email` | `preserve_domain: true` |
| `users.full_name` | `neosync_fullname` | — |
| `users.phone` | `phone_number` | `prefix: "010-"`, 길이 13, deterministic |

변환기 이름과 파라미터는 pgstream `0a58e63`의 `docs/transformers.md`에서 확인했습니다.

### CLI 도움말에 없는 것

- `xata clone config --mode auto`는 이 스키마에 대해 **규칙을 하나도 만들지 않았습니다**(`table_transformers: []`).
  개인정보 컬럼을 탐지하려면 `--mode ai`가 필요하고, 이 모드는 스키마를 LLM으로 보냅니다(기본값
  `--model gemini-2.5-flash`). 그래서 이 실습의 규칙은 손으로 썼습니다.
- `clone start`는 작업 디렉터리의 `.xata/clone.yaml`에서 규칙을 읽습니다. `--config`는 pgstream 설정 전체를
  받는 옵션이라, 여기에 `clone.yaml`을 주면
  *unsupported postgres source mode, must be one of 'replication', 'snapshot' or 'snapshot_and_replication'*로 실패합니다.
- `.xata/`가 없으면 `config`가 `ENOENT … .xata/clone.yaml`로 실패합니다.

### 확인과 결과

첫 실행 2026-10-28(M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, pgstream 1.5.0). 사용자 10만 명과
주문 30만 건을 clone하는 데 3.6–3.8초가 걸렸고 모든 확인을 통과했습니다.

| 확인 | 결과 |
|---|---|
| 행 수가 원본과 같음 | 100,000 / 300,000 |
| seed 공식과 같은 email·이름·전화 | 0 / 0 / 0. 익명화하지 않은 `base`로 한 양성 대조는 각각 100,000 |
| email 도메인 유지, email 고유성 | 유지됨. 서로 다른 값 100,000개 |
| `orders.amount` 합계와 외래 키 | 원본과 같음. 고아 행 0 |
| 예시, 사용자 42 | `user42@example.com \| Park Hayun \| 010-0042-0294` → `e755e10c…@example.com \| Siu Kuilder \| 010-986763646` |

pgstream은 경고 세 개를 남겼습니다.
- unique index가 있는 컬럼인데 `neosync_email`이 고유한 출력을 보장하지 않습니다.
- 복원 중 `pg_stat_statements`가 `public` 스키마에 의존해서 `DROP SCHEMA public`이 실패했습니다.
- 복원 중 스키마가 이미 있어서 `CREATE SCHEMA public`이 실패했습니다.

뒤의 두 경고는 무시됐고 결과에는 영향이 없었습니다.

**클린 실행**, 2026-10-28: 확인 9/9 통과. clone은 3.2초가 걸렸고 경고 세 개는 같았습니다.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
