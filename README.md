# Xata Hands-on Labs

[English](#english) | [한국어](#한국어)

## English

Self-test labs for the open-source [Xata](https://github.com/xataio/xata) platform: Postgres
branching, scale-to-zero, pgroll schema migrations, SQL over HTTP, anonymized clones, and many
parallel "agents" each working on their own branch. Everything runs on a laptop in a kind
cluster, exactly as upstream's README brings it up, plus three local adjustments listed below.

### What Xata OSS is (read from the source at the pinned commit)

- A platform for many Postgres databases on Kubernetes, Apache 2.0, the same code that runs Xata Cloud.
  It is built on CloudNativePG — a Xata fork, image `ghcr.io/xataio/xata-cnpg/cloudnative-pg` — and adds an
  SQL gateway (routing, waking sleeping branches, SQL over HTTP), a branch operator, a control plane
  with a REST API and CLI, Keycloak for auth, and a scale-to-zero CNPG plugin.
- **The engine is plain Postgres.** Branches run `ghcr.io/xataio/postgres-images/cnpg-postgres-plus:18.6`,
  which reports `PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2)`, the community PGDG build. 73 extensions
  are available (pgvector 0.8.7, PostGIS 3.6.4, pg_cron, pg_partman, pg_repack, …); Xata adds one
  preload library, `xatautils`. The application role `xata` is not a superuser (it has
  `CREATEROLE`, `CREATEDB` and `REPLICATION`).
- **Branching happens below Postgres.** A branch is a clone of its parent's volume: a CSI volume
  snapshot, or `XVolClone` on Xata's own storage (`services/branch-operator/api/v1alpha1/branch_types.go`).
  Postgres simply starts on the copied data directory, which is why extensions keep working.
  Neon, by contrast, replaces Postgres' storage layer.
- **"Built for AI agents"** — one isolated, cheap, disposable branch per agent — is Xata's own claim
  ([docs](https://xata.io/docs/ai-agents/overview), [blog](https://xata.io/blog/database-branching-for-ai-coding-agents)).
  No independent benchmark was found (2026-10-28), so [`oss/agent-fanout`](oss/agent-fanout/) measures it.

### 🧪 Labs (`oss/`)

| Lab | Scenario | What it shows |
|-----|----------|---------------|
| [oss/platform](oss/platform/) | S1–S2 | Bring-up on kind + tilt, CLI login, the `hols` project; engine, extensions and role rights |
| [oss/branching](oss/branching/) | S3–S5 | Branch time vs data size, two-way isolation, a three-level tree whose middle branch is deleted |
| [oss/scale-to-zero](oss/scale-to-zero/) | S6 | Hibernation after inactivity and wake-on-connect latency, and what keeps a branch awake |
| [oss/pgroll-migrations](oss/pgroll-migrations/) | S7 | A zero-downtime migration on a branch: old and new schema both usable mid-migration |
| [oss/agent-fanout](oss/agent-fanout/) | S8 | N agents in parallel: branch → migrate → test → delete; the per-project lock and the memory ceiling |
| [oss/serverless-sql](oss/serverless-sql/) | S9 | `POST /sql` on the gateway with the connection string in a header |
| [oss/anonymized-clone](oss/anonymized-clone/) | S10 | `xata clone` from a plain Postgres into a branch with PII replaced on the way in |

### Findings

1. **Branch NetworkPolicy blocks the API server and the backup store on kind.** branch-operator
   gives every branch a policy that allows egress only to its own pods and DNS
   (`services/branch-operator/pkg/reconciler/resources/resources.go`). kind's kindnet enforces it,
   so the CNPG instance manager cannot reach the API server (branches do not start) and backups
   cannot reach RustFS (they fail and retry). The failing backup holds a connection, and that
   connection keeps scale-to-zero from ever triggering. One extra policy fixes both
   ([manifest](oss/platform/manifests/allow-branch-egress.yaml.tmpl)).
   Reported upstream 2026-10-30: [xataio/xata#16](https://github.com/xataio/xata/issues/16).
2. **Concurrent branch creates in one project can fail.** Creation takes a per-project advisory lock
   without waiting (`TryAcquireProjectLock`, `services/projects/branch/create.go`), so a parallel
   create gets HTTP 429 `project … is busy; retry later` with `Retry-After: 1`. CLI 1.18.0 does retry
   it: its API client (`@xata.io/api` 0.1.28, `src/retry.ts`) makes up to 3 attempts, about 2 s in all.
   That can end before the lock is released. In both N=6 runs, one create still failed after 3.3–3.5 s.
   Agents need a longer retry of their own. *(Corrected 2026-10-30: an earlier version said the CLI does not retry.)*
   Reported upstream 2026-10-30: [xataio/xata#17](https://github.com/xataio/xata/issues/17).
3. **Memory is the density limit.** Each `xata.micro` branch requests about 1 GiB (924 Mi Postgres +
   100 Mi pooler). With the platform taking about 7 GiB of requests, a 16 GiB node holds only a few branches at once. More
   agents than that wait for a slot, and start one after another rather than in parallel. Each new branch's
   snapshot-recovery job requests as much as its Postgres pod, so a burst of creates briefly needs about twice
   that. One clean-run fan-out stalled for about 15 minutes on memory ([agent-fanout](oss/agent-fanout/)).
4. **Local branch timings are not CoW numbers.** The CSI hostpath driver snapshots with `tar czf` and
   restores with `tar zxvf`, so branch time grows with data size. The same driver's snapshot is not
   atomic: one child of a parent with recent writes could not boot
   (`PANIC: could not locate a valid checkpoint record`). The labs run `CHECKPOINT` on the parent first.
   A CoW driver cannot run on Docker Desktop's kernel (no dm-thin), and OSS has no `XVolClone` storage.
   A reflink copy of 1.4 GB took 0.03 s against 30 s for the `tar` round trip ([branching](oss/branching/#could-cow-run-here-2026-10-30)).

### Prerequisites

- Docker with **16 GiB** of memory (Docker Desktop → Settings → Resources). At 8 GiB, the platform alone
  uses 99% of the node's memory requests and no branch schedules.
- `kind`, `tilt`, `helm`, `kubectl`, `psql` (`libpq`), `envsubst` (`gettext`), `jq`, `python3`:
  `brew install kind tilt-dev/tap/tilt helm kubectl libpq gettext jq`
- The Xata CLI: `curl -fsSL https://xata.io/install.sh | XATA_NO_MODIFY_PATH=1 bash` (installs to `~/.config/xata/bin`, with pgroll and pgstream)

### Quick start

```bash
git config core.hooksPath .githooks
oss/platform/up.sh      # ~25 min with a cold image cache, ~17 min warm
oss/run-all.sh          # every lab in order: ~20–30 min (scale-to-zero alone waits ~7 min)
oss/platform/down.sh    # delete the cluster
```

### Local deviations

What these labs change on upstream's local deployment, and why:

| Change | Where | Why |
|---|---|---|
| Keycloak at 1 instance instead of 2 | `up.sh` | Frees 2 GiB of requests. The labs log in with an API key |
| `hols-allow-branch-egress` NetworkPolicy | `up.sh`, `manifests/` | Finding 1 |
| `CHECKPOINT` on the parent before every branch | `env.sh` `new_branch` | Finding 4. `NO_CHECKPOINT=1` shows the raw behaviour |

### Repository checks

- `git config core.hooksPath .githooks` once per clone: gitleaks, host paths, syntax and ignore rules on every commit.
- Pull requests run one CI job, `guard`. Links and syntax run by hand: `gh workflow run checks.yml --ref <branch>`.

## 한국어

오픈소스 [Xata](https://github.com/xataio/xata) 플랫폼을 직접 검증하는 실습입니다. 다루는 기능은 Postgres
브랜칭, scale-to-zero, pgroll 스키마 마이그레이션, HTTP SQL, 익명화 clone, 그리고 각자 브랜치에서 일하는
병렬 "에이전트"입니다. 모두 노트북의 kind 클러스터에서 upstream README와 같은 방법으로 띄웁니다. 여기에
아래에 적은 로컬 조정 세 가지를 더했습니다.

### Xata OSS란 (고정한 커밋의 소스에서 확인)

- Kubernetes 위에서 많은 Postgres를 운영하는 플랫폼입니다. Apache 2.0이고, Xata Cloud와 같은 코드입니다.
  CloudNativePG의 Xata fork(`ghcr.io/xataio/xata-cnpg/cloudnative-pg`)를 기반으로 다음을 더했습니다.
  - SQL gateway: 라우팅, 잠든 브랜치 깨우기, HTTP SQL
  - branch operator
  - REST API와 CLI를 갖춘 control plane
  - Keycloak 인증
  - scale-to-zero CNPG 플러그인
- **엔진은 일반 Postgres입니다.** 브랜치는 `ghcr.io/xataio/postgres-images/cnpg-postgres-plus:18.6`으로 뜨고,
  버전은 커뮤니티 PGDG 빌드인 `PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2)`입니다.
  - 확장 73개를 쓸 수 있습니다(pgvector 0.8.7, PostGIS 3.6.4, pg_cron, pg_partman, pg_repack 등).
  - Xata가 더한 preload 라이브러리는 `xatautils` 하나입니다.
  - 앱 역할 `xata`는 superuser가 아닙니다. `CREATEROLE`, `CREATEDB`, `REPLICATION` 권한은 있습니다.
- **브랜칭은 Postgres 아래 계층에서 일어납니다.** 브랜치는 부모 볼륨의 복제본입니다. CSI 볼륨 스냅샷을
  쓰거나, Xata 자체 스토리지에서는 `XVolClone`을 씁니다(`services/branch-operator/api/v1alpha1/branch_types.go`).
  Postgres는 복사된 데이터 디렉터리에서 그냥 기동하므로 확장이 그대로 동작합니다. 반면 Neon은 Postgres의
  스토리지 계층 자체를 교체했습니다.
- **"AI 에이전트에 적합"**, 즉 에이전트마다 격리되고 저렴하고 버릴 수 있는 브랜치를 준다는 주장은 Xata의
  자체 주장입니다([문서](https://xata.io/docs/ai-agents/overview), [블로그](https://xata.io/blog/database-branching-for-ai-coding-agents)).
  2026-10-28 기준으로 독립 벤치마크를 찾지 못해서 [`oss/agent-fanout`](oss/agent-fanout/)에서 직접 측정합니다.

### 🧪 실습 (`oss/`)

| 실습 | 시나리오 | 보여주는 것 |
|-----|----------|-------------|
| [oss/platform](oss/platform/) | S1–S2 | kind와 tilt로 기동, CLI 로그인, `hols` 프로젝트 생성. 엔진, 확장, 역할 권한 확인 |
| [oss/branching](oss/branching/) | S3–S5 | 데이터 크기별 브랜치 시간, 양방향 격리, 중간 브랜치를 지운 3단 트리 |
| [oss/scale-to-zero](oss/scale-to-zero/) | S6 | 비활성 후 hibernation, 접속 시 깨어나는 지연, 브랜치를 깨어 있게 만드는 요인 |
| [oss/pgroll-migrations](oss/pgroll-migrations/) | S7 | 브랜치에서 무중단 마이그레이션. 진행 중에 구 스키마와 신 스키마 모두 사용 가능 |
| [oss/agent-fanout](oss/agent-fanout/) | S8 | 에이전트 N개 병렬 실행(브랜치 → 마이그레이션 → 테스트 → 삭제). 프로젝트 잠금과 메모리 한계 |
| [oss/serverless-sql](oss/serverless-sql/) | S9 | 접속 문자열을 헤더에 넣어 gateway의 `POST /sql` 호출 |
| [oss/anonymized-clone](oss/anonymized-clone/) | S10 | 일반 Postgres에서 브랜치로 `xata clone`하면서 개인정보 교체 |

### 발견 사항

1. **kind에서는 브랜치 NetworkPolicy가 API 서버와 백업 저장소를 막습니다.** branch-operator는 브랜치마다
   egress를 자기 pod와 DNS로만 허용하는 정책을 만듭니다(`services/branch-operator/pkg/reconciler/resources/resources.go`).
   kind의 kindnet은 이 정책을 실제로 집행합니다. 그 결과는 다음과 같습니다.
   - CNPG instance manager가 API 서버에 닿지 못해 브랜치가 뜨지 않습니다.
   - 백업이 RustFS에 닿지 못해 실패와 재시도를 반복합니다.
   - 실패하는 백업이 연결을 붙잡고 있어서 scale-to-zero가 끝내 동작하지 않습니다.

   정책 하나를 추가하면 두 문제가 모두 풀립니다([manifest](oss/platform/manifests/allow-branch-egress.yaml.tmpl)).
   2026-10-30 업스트림에 보고했습니다: [xataio/xata#16](https://github.com/xataio/xata/issues/16).
2. **같은 프로젝트에서 브랜치를 동시에 만들면 실패할 수 있습니다.** 생성은 프로젝트 단위 advisory lock을
   기다리지 않고 한 번만 시도합니다(`TryAcquireProjectLock`, `services/projects/branch/create.go`). 그래서
   동시에 생성하면 `Retry-After: 1`과 함께 HTTP 429 `project … is busy; retry later`가 돌아옵니다. CLI 1.18.0은
   이것을 재시도합니다. API 클라이언트(`@xata.io/api` 0.1.28, `src/retry.ts`)가 최대 3번 시도하는데, 기다리는 시간은 모두 합해 약 2초입니다.
   이 시간은 잠금이 풀리기 전에 끝날 수 있습니다. N=6 실행 두 번 모두 생성 하나가 3.3–3.5초 만에 실패했습니다.
   에이전트는 이보다 긴 재시도를 직접 해야 합니다. *(2026-10-30 정정: 이전 판은 CLI가 재시도하지 않는다고 적었습니다.)*
   2026-10-30 업스트림에 보고했습니다: [xataio/xata#17](https://github.com/xataio/xata/issues/17).
3. **밀도의 한계는 메모리입니다.** `xata.micro` 브랜치 하나가 약 1 GiB를 요청합니다(Postgres 924 Mi와
   pooler 100 Mi). 플랫폼이 요청량 약 7 GiB를 차지하므로, 16 GiB 노드에는 브랜치가 몇 개밖에 동시에
   올라가지 않습니다. 그보다 많은 에이전트는 자리가 날 때까지 기다리고, 병렬이 아니라 차례로 시작합니다.
   새 브랜치의 snapshot-recovery job도 Postgres pod만큼 메모리를 요청하므로, 생성이 몰리면 잠깐 약 두 배가
   필요합니다. 클린 실행에서 fan-out 한 번이 메모리 때문에 약 15분 정체됐습니다([agent-fanout](oss/agent-fanout/)).
4. **로컬 브랜치 시간은 CoW 수치가 아닙니다.** CSI hostpath 드라이버는 `tar czf`로 스냅샷을 만들고
   `tar zxvf`로 복원합니다. 그래서 브랜치 시간이 데이터 크기에 비례합니다. 같은 이유로 스냅샷이 원자적이지
   않습니다. 최근 쓰기가 있던 부모에서 만든 자식 하나가 부팅하지 못했습니다
   (`PANIC: could not locate a valid checkpoint record`). 그래서 실습은 브랜치 전에 부모에서 `CHECKPOINT`를 실행합니다.
   Docker Desktop 커널에서는 CoW 드라이버를 돌릴 수 없고(dm-thin 없음), OSS에는 `XVolClone` 스토리지가 없습니다.
   1.4 GB를 reflink로 복사하면 0.03초, `tar`로 묶고 풀면 30초였습니다([branching](oss/branching/#여기서-cow를-돌릴-수-있나2026-10-30)).

### 준비물

- 메모리 **16 GiB**를 준 Docker(Docker Desktop → Settings → Resources). 8 GiB에서는 플랫폼만으로 노드
  메모리 요청량이 99%가 되어 브랜치가 하나도 스케줄되지 않습니다.
- `kind`, `tilt`, `helm`, `kubectl`, `psql`(`libpq`), `envsubst`(`gettext`), `jq`, `python3`:
  `brew install kind tilt-dev/tap/tilt helm kubectl libpq gettext jq`
- Xata CLI: `curl -fsSL https://xata.io/install.sh | XATA_NO_MODIFY_PATH=1 bash`. `~/.config/xata/bin`에
  pgroll, pgstream과 함께 설치됩니다.

### 빠른 시작

```bash
git config core.hooksPath .githooks
oss/platform/up.sh      # 이미지 캐시가 없으면 약 25분, 있으면 약 17분
oss/run-all.sh          # 모든 실습을 순서대로 실행, 약 20–30분(scale-to-zero만 약 7분 대기)
oss/platform/down.sh    # 클러스터 삭제
```

### 로컬 조정

이 실습이 upstream 로컬 배포에서 바꾼 것과 그 이유입니다.

| 변경 | 위치 | 이유 |
|---|---|---|
| Keycloak 인스턴스 2개를 1개로 | `up.sh` | 요청량 2 GiB 확보. 실습은 API 키로 로그인 |
| `hols-allow-branch-egress` NetworkPolicy 추가 | `up.sh`, `manifests/` | 발견 사항 1 |
| 브랜치 만들기 전 부모에서 `CHECKPOINT` | `env.sh`의 `new_branch` | 발견 사항 4. `NO_CHECKPOINT=1`이면 원래 동작 |

### 저장소 검사

- 클론마다 한 번 `git config core.hooksPath .githooks`를 실행합니다. 커밋할 때마다 gitleaks, 호스트 경로,
  문법, ignore 규칙을 검사합니다.
- PR에서는 CI job `guard` 하나만 돕니다. 링크와 문법 검사는 손으로 실행합니다:
  `gh workflow run checks.yml --ref <branch>`.
