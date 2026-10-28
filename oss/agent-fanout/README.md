# agent-fanout — N agents, one branch each (S8)

[English](#english) | [한국어](#한국어)

## English

Xata pitches branches as sandboxes for AI agents: each agent gets an isolated copy of the
database, breaks it, and throws it away ([vendor docs](https://xata.io/docs/ai-agents/overview), read 2026-10-28).
This lab runs that loop with N shell "agents" at once and records what happens.

`agent.sh <n>` is one agent:

1. `xata branch create agent-<n> --parent-branch base`
2. wait until healthy (bounded)
3. **migrate**: `alter table users add column agent_<n> int` and fill 10,000 rows
4. **test**: the column holds 10,000 values; `orders` still has 300,000 rows
5. **isolation**: no other agent's `agent_*` column is visible
6. `xata branch delete`

`fanout.sh <N>` starts N of them in parallel and checks that `base`'s schema is unchanged afterwards.
`RETRY_BUSY=<k>` lets each agent retry a busy-project error up to k times (see below).

### Results

First run, 2026-10-28 (M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, `xata.micro`, `base` = 50 MB;
raw lines in `results/`):

| N | Retry on busy | Succeeded | Wall time | Branch healthy (median / max) | Work per agent | Delete |
|---|---|---|---|---|---|---|
| 1 | no | 1/1 | 50.2 s | 43.8 s | 4.8 s | 0.9 s |
| 3 | no | 3/3 | 51.1 s | 45.1 / 45.2 s | 4.4–4.6 s | 0.8–0.9 s |
| 6 | no | **5/6** | 102.3 s | 74.2 / 96.0 s | 4.1–4.7 s | 0.8–1.2 s |
| 6 | up to 5 | 6/6 | 105.5 s | 76.8 / 98.3 s | 4.0–7.1 s | 0.9–1.8 s |

Every successful agent saw only its own column, and `base` was unchanged after every run.

**What limits fan-out**

- **The project lock.** The failed agent got
  `acquire project lock: project [prj_…] is busy; retry later` 3.5 s in. Branch creation holds a
  per-project PostgreSQL advisory lock around provisioning, taken without waiting
  (`TryAcquireProjectLock`, `services/projects/branch/create.go`; the API adds a `Retry-After`
  header). The CLI does not retry, so the agent's harness has to. In the run with retries enabled,
  no agent needed one: the collision depends on timing.
- **Memory.** Up to 3 agents, branches became healthy in about 45 s, about the same as a single one. At 6
  they finished one after another (54, 66, 74, 85, 96 s). Each `xata.micro` branch requests 1 GiB, and
  with `main`, `base` and one more branch already up, the node had 75% of its 16 GiB requested
  (12,016 Mi), room for three or four more. The rest waited in `Pending`. On this
  machine, the number of agents that really run in parallel is set by node memory, not by Xata.

**Clean run**, 2026-10-28 (`run-all.sh`, then one more N=6 alone with a 15 s sampler of node memory requests and pods):

| Run (UTC) | N | Retry on busy | Succeeded | Wall time | Branch healthy (median / max) |
|---|---|---|---|---|---|
| 15:57 | 3 | no | 3/3 | 54.0 s | 48.4 / 48.4 s |
| 15:58 | 6 | no | **5/6** (busy) | 100.6 s | 52.5 / 95.7 s |
| 16:00 | 6 | up to 5 | **5/6** | **1061.6 s** | 903.4 / 1047.0 s |
| 16:28 | 6 | up to 5 | 6/6 (one agent retried once) | 116.4 s | 73.3 / 102.6 s |

The 16:00 run stalled. From 16:04 to 16:14, branch pods sat in `Pending — Insufficient memory`, and one agent gave up at the 600 s `wait_ready` limit. What held the memory was not recorded, and the run did not reproduce at 16:28. In that run, node memory requests peaked at 15,516 Mi (`results/sampler-20261028T162841Z.txt`), almost all of the node's 16 GiB. The snapshot-recovery job of each new branch requests the same memory as its Postgres pod, so a burst of creates needs about twice the steady-state memory for a moment.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

Xata는 브랜치를 AI 에이전트용 샌드박스로 내세웁니다. 에이전트마다 격리된 DB 복사본을 받아 마음대로 망가뜨리고
버리면 된다는 것입니다([벤더 문서](https://xata.io/docs/ai-agents/overview), 2026-10-28 확인). 이 실습은
그 흐름을 셸 "에이전트" N개로 동시에 돌리고 무슨 일이 일어나는지 기록합니다.

`agent.sh <n>`이 에이전트 하나입니다.

1. `xata branch create agent-<n> --parent-branch base`
2. healthy가 될 때까지 대기(제한 시간 있음)
3. **마이그레이션**: `alter table users add column agent_<n> int`, 1만 행 채우기
4. **테스트**: 컬럼에 값이 1만 개 있고, `orders`는 여전히 30만 행인지 확인
5. **격리**: 다른 에이전트의 `agent_*` 컬럼이 보이지 않는지 확인
6. `xata branch delete`

`fanout.sh <N>`은 이를 N개 동시에 시작하고, 끝난 뒤 `base` 스키마가 그대로인지 확인합니다.
`RETRY_BUSY=<k>`를 주면 각 에이전트가 프로젝트 busy 오류를 최대 k번 재시도합니다(아래 참고).

### 결과

첫 실행 2026-10-28. 조건은 M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, `xata.micro`, `base` 50 MB이고,
원본 기록은 `results/`에 있습니다.

| N | busy 재시도 | 성공 | 전체 시간 | 브랜치 healthy(중앙값 / 최대) | 에이전트당 작업 | 삭제 |
|---|---|---|---|---|---|---|
| 1 | 없음 | 1/1 | 50.2초 | 43.8초 | 4.8초 | 0.9초 |
| 3 | 없음 | 3/3 | 51.1초 | 45.1 / 45.2초 | 4.4–4.6초 | 0.8–0.9초 |
| 6 | 없음 | **5/6** | 102.3초 | 74.2 / 96.0초 | 4.1–4.7초 | 0.8–1.2초 |
| 6 | 최대 5번 | 6/6 | 105.5초 | 76.8 / 98.3초 | 4.0–7.1초 | 0.9–1.8초 |

성공한 에이전트는 모두 자기 컬럼만 봤고, 모든 실행 뒤에 `base`는 그대로였습니다.

**fan-out을 제한하는 요인**

- **프로젝트 잠금.** 실패한 에이전트는 3.5초 만에
  `acquire project lock: project [prj_…] is busy; retry later`를 받았습니다. 브랜치 생성은 프로비저닝하는
  동안 프로젝트 단위 PostgreSQL advisory lock을 잡는데, 이 잠금을 기다리지 않고 한 번만 시도합니다
  (`TryAcquireProjectLock`, `services/projects/branch/create.go`. API는 `Retry-After` 헤더를 붙임). CLI가
  재시도하지 않으므로 에이전트를 돌리는 쪽이 재시도해야 합니다. 재시도를 켠 실행에서는 실제로 재시도한
  에이전트가 없었습니다. 충돌 여부는 타이밍에 달려 있습니다.
- **메모리.** 3개까지는 브랜치가 하나일 때와 비슷하게 약 45초에 healthy가 됐습니다. 6개에서는 차례로
  끝났습니다(54, 66, 74, 85, 96초). `xata.micro` 브랜치 하나가 1 GiB를 요청합니다. `main`, `base`와 브랜치
  하나가 이미 떠 있는 상태에서 노드의 16 GiB 중 75%(12,016 Mi)가 요청돼 있어서, 3–4개분의 자리만 남아
  있었습니다. 나머지는 `Pending`으로 기다렸습니다. 이 머신에서 실제로 동시에 도는 에이전트 수는
  Xata가 아니라 노드 메모리가 정합니다.

**클린 실행**, 2026-10-28(`run-all.sh` 뒤에, 노드 메모리 요청량과 pod를 15초마다 기록하면서 N=6을 한 번 더 단독 실행):

| 실행(UTC) | N | busy 재시도 | 성공 | 전체 시간 | 브랜치 healthy(중앙값 / 최대) |
|---|---|---|---|---|---|
| 15:57 | 3 | 없음 | 3/3 | 54.0초 | 48.4 / 48.4초 |
| 15:58 | 6 | 없음 | **5/6**(busy) | 100.6초 | 52.5 / 95.7초 |
| 16:00 | 6 | 최대 5번 | **5/6** | **1061.6초** | 903.4 / 1047.0초 |
| 16:28 | 6 | 최대 5번 | 6/6(한 에이전트가 1번 재시도) | 116.4초 | 73.3 / 102.6초 |

16:00 실행은 정체됐습니다. 16:04부터 16:14까지 브랜치 pod가 `Pending — Insufficient memory`로 기다렸고, 에이전트 하나는 `wait_ready`의 600초 제한에 걸려 포기했습니다. 무엇이 메모리를 잡고 있었는지는 기록되지 않았고, 16:28 실행에서는 재현되지 않았습니다. 그 실행에서 노드 메모리 요청량은 정점에서 15,516 Mi로(`results/sampler-20261028T162841Z.txt`), 노드의 16 GiB를 거의 다 썼습니다. 새 브랜치마다 뜨는 snapshot-recovery job도 Postgres pod와 같은 메모리를 요청합니다. 그래서 생성이 몰리면 잠깐이나마 평소의 약 두 배 메모리가 필요합니다.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
