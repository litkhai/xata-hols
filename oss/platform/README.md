# platform — bring-up, login, the `hols` project (S1–S2)

[English](#english) | [한국어](#한국어)

## English

`up.sh` follows upstream's README (kind cluster, then `tilt up` in a checkout of
[xataio/xata](https://github.com/xataio/xata) at the commit pinned as `XATA_REF`), applies the three
[local deviations](../../README.md#local-deviations), logs the CLI in with a dev API key, and creates
the project every other lab uses:

| Branch | Kind | Contents | Scale-to-zero |
|---|---|---|---|
| `main` | root, created with the project | empty | off |
| `base` | root | [`seed-app.sql`](seed-app.sql): 100,000 users with generated personal data, 300,000 orders (50 MB) | off |

Child branches inherit the project default: scale-to-zero on after 5 minutes.

### Files

| File | Does |
|---|---|
| `up.sh` | Idempotent bring-up. Checks Docker has 16 GiB first |
| `down.sh` | Stops tilt, deletes the cluster. `--all` also removes `$XATA_HOLS_STATE` |
| `env.sh` | Sourced by every script: CLI path, `q <branch> <sql>`, `new_branch`, a bounded `wait_ready` |
| `manifests/allow-branch-egress.yaml.tmpl` | The extra NetworkPolicy ([Finding 1](../../README.md#findings)) |
| `seed-app.sql` | The `base` data set |

### What the CLI needs on this deployment

`xata project create` and `xata branch create` prompt for anything not given, and an agent
cannot answer a prompt. On this deployment, these were the values that worked (2026-10-28, CLI 1.18.0):

| Flag | Value | Where it comes from |
|---|---|---|
| `--region` | `local` | `kustomize/overlays/local/projects/values.yaml` (`defaultRegion`) |
| `--instance-type` | `xata.micro` (0.25 vCPU request, 1 GiB) | `services/projects/store/store.go` (`InstanceTypes`) |
| `--postgres-version` | `postgres:18.6` (not `18` or `18.6`) | `internal/postgresversions/versions.yaml`, name `<image>:<minor>` |
| `--replicas` | `0` | — |

The API is on `http://localhost:5081` (upstream's README says 5001; the Tiltfile moved it off
macOS AirPlay). The gateway serves each branch at `<branch-id>.127.0.0.1.nip.io:7654` (Postgres,
TLS) and `:8443` (HTTP).

### Results

**S1 bring-up**, first run on 2026-10-28 (MacBook M4 Pro, 12 CPUs, Docker Desktop, kind v0.33.0 /
Kubernetes v1.37.0): tilt took about 25 minutes from `tilt up` until every resource was ready,
including building the six service images. That left 38 pods and about 4.5 GiB of node memory in use.
With Docker at 7.7 GiB, the platform's memory requests reached 7,864 Mi (99%), and the first branch's
pod stayed `Pending — Insufficient memory`. Hence the 16 GiB prerequisite.

**S2 the engine**, read from `main` through the gateway:

| Check | Result |
|---|---|
| `select version()` | `PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2) on aarch64-unknown-linux-gnu` |
| Image | `ghcr.io/xataio/postgres-images/cnpg-postgres-plus:18.6`; operator `ghcr.io/xataio/xata-cnpg/cloudnative-pg:g228b764` |
| Role `xata` | `rolsuper = f`, `rolcreaterole = t`, `rolcreatedb = t`, `rolreplication = t`; member of `pg_checkpoint` |
| `pg_available_extensions` | 73, including vector 0.8.7, postgis 3.6.4, pg_cron 1.6, pg_partman 5.4.1, pg_repack 1.5.3, pgaudit 18.0, hypopg 1.4.2, pg_hint_plan 1.8.0 |
| `shared_preload_libraries` | `xatautils,pg_stat_statements,auto_explain` |
| Root branch create → healthy | 31.8 s (empty, 10 GB volume) |

**Clean run**, 2026-10-28 15:37–15:53 UTC (same machine; Docker image cache warm): `up.sh` took 16 min 31 s, of which tilt 755 s. The extra NetworkPolicy went in before the first branch, and `main` and `base` came up with no manual step.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

`up.sh`는 upstream README의 절차를 따릅니다. kind 클러스터를 만들고, `XATA_REF`로 고정한 커밋의
[xataio/xata](https://github.com/xataio/xata) 체크아웃에서 `tilt up`을 실행합니다. 이어서
[로컬 조정](../../README.md#로컬-조정) 세 가지를 적용하고, 개발용 API 키로 CLI에 로그인한 뒤, 다른 모든
실습이 쓰는 프로젝트를 만듭니다.

| 브랜치 | 종류 | 내용 | Scale-to-zero |
|---|---|---|---|
| `main` | 루트, 프로젝트와 함께 생성 | 비어 있음 | 끔 |
| `base` | 루트 | [`seed-app.sql`](seed-app.sql): 생성한 개인정보를 담은 사용자 10만 명, 주문 30만 건(50 MB) | 끔 |

자식 브랜치는 프로젝트 기본값을 물려받아, 5분 동안 쓰지 않으면 scale-to-zero로 내려갑니다.

### 파일

| 파일 | 역할 |
|---|---|
| `up.sh` | 멱등 기동. 먼저 Docker 메모리가 16 GiB인지 확인 |
| `down.sh` | tilt를 멈추고 클러스터 삭제. `--all`이면 `$XATA_HOLS_STATE`도 삭제 |
| `env.sh` | 모든 스크립트가 source. CLI 경로, `q <branch> <sql>`, `new_branch`, 제한 시간이 있는 `wait_ready` |
| `manifests/allow-branch-egress.yaml.tmpl` | 추가 NetworkPolicy([발견 사항 1](../../README.md#발견-사항)) |
| `seed-app.sql` | `base` 데이터 |

### 이 배포에서 CLI에 필요한 값

`xata project create`와 `xata branch create`는 빠진 값을 대화형으로 묻는데, 에이전트는 이 질문에 답할 수
없습니다. 이 배포에서 동작한 값은 다음과 같습니다(2026-10-28, CLI 1.18.0).

| 플래그 | 값 | 출처 |
|---|---|---|
| `--region` | `local` | `kustomize/overlays/local/projects/values.yaml`(`defaultRegion`) |
| `--instance-type` | `xata.micro`(vCPU 요청 0.25, 1 GiB) | `services/projects/store/store.go`(`InstanceTypes`) |
| `--postgres-version` | `postgres:18.6`(`18`이나 `18.6`은 거부됨) | `internal/postgresversions/versions.yaml`, 이름 형식 `<image>:<minor>` |
| `--replicas` | `0` | — |

API는 `http://localhost:5081`에 있습니다. upstream README는 5001이라고 적었지만, Tiltfile이 macOS
AirPlay 포트를 피해 옮겼습니다. gateway는 브랜치마다 `<branch-id>.127.0.0.1.nip.io:7654`(Postgres, TLS)와
`:8443`(HTTP)으로 서비스합니다.

### 결과

**S1 기동**: 첫 실행 2026-10-28(MacBook M4 Pro, CPU 12개, Docker Desktop, kind v0.33.0 / Kubernetes v1.37.0).
- `tilt up`부터 모든 리소스 준비까지 약 25분이 걸렸습니다. 서비스 이미지 6개의 빌드 시간이 포함됩니다.
- 완료 후 pod 38개가 떠 있었고 노드 메모리 약 4.5 GiB를 썼습니다.
- Docker 메모리가 7.7 GiB일 때는 플랫폼의 메모리 요청이 7,864 Mi(99%)에 이르렀습니다. 그래서 첫 브랜치 pod가
  `Pending — Insufficient memory`에 머물렀고, 이것이 16 GiB를 준비물로 둔 이유입니다.

**S2 엔진**: gateway를 거쳐 `main`에서 조회했습니다.

| 확인 | 결과 |
|---|---|
| `select version()` | `PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2) on aarch64-unknown-linux-gnu` |
| 이미지 | `ghcr.io/xataio/postgres-images/cnpg-postgres-plus:18.6`, operator `ghcr.io/xataio/xata-cnpg/cloudnative-pg:g228b764` |
| 역할 `xata` | `rolsuper = f`, `rolcreaterole = t`, `rolcreatedb = t`, `rolreplication = t`, `pg_checkpoint` 멤버 |
| `pg_available_extensions` | 73개. vector 0.8.7, postgis 3.6.4, pg_cron 1.6, pg_partman 5.4.1, pg_repack 1.5.3, pgaudit 18.0, hypopg 1.4.2, pg_hint_plan 1.8.0 포함 |
| `shared_preload_libraries` | `xatautils,pg_stat_statements,auto_explain` |
| 루트 브랜치 생성 → healthy | 31.8초(빈 DB, 10 GB 볼륨) |

**클린 실행**, 2026-10-28 15:37–15:53 UTC(같은 머신, Docker 이미지 캐시 있음): `up.sh`는 16분 31초가 걸렸고 그중 tilt가 755초였습니다. 추가 NetworkPolicy가 첫 브랜치보다 먼저 들어갔고, `main`과 `base`는 손댈 일 없이 올라왔습니다.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
