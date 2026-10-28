# scale-to-zero — hibernate and wake (S6)

[English](#english) | [한국어](#한국어)

## English

Child branches in the `hols` project hibernate after 5 idle minutes (`up.sh` sets
`--scale-to-zero-child true --inactivity-period-child 5`). `run.sh` creates a child of `base`,
runs one query, watches until its Postgres pod is gone, then connects again and times the first
query. The whole lab takes about 7 minutes.

### How "idle" is decided

A sidecar in every branch pod (`services/scale-to-zero-sidecar/internal/sidecar/scale_to_zero.go`)
counts connections once a minute:

```sql
SELECT COUNT(*) FROM pg_stat_activity
WHERE state IN ('active', 'idle', 'idle in transaction')
  AND pg_backend_pid() != pg_stat_activity.pid AND usename != 'streaming_replica';
```

**An idle connection counts as activity.** Any client that keeps a connection open (a pool, an
IDE, a forgotten `psql`) keeps the branch awake. When the count has been zero for the inactivity
period, the sidecar sets hibernation on the Branch resource, and the pod is removed. The gateway
wakes the branch on the next connection.

### Results

First run, 2026-10-28 (M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0), grandchild branch `g1`:

| Event | Time |
|---|---|
| Last client query | 14:54:21 |
| Still awake 10 minutes later: the sidecar logged `open connections count: 1` every minute | — |
| The one connection: `barman_cloud_backup`, idle since the pod started | — |

The backup that opened it was failing: `ConnectTimeoutError … http://rustfs-eu:9000/backups/…`.
The branch NetworkPolicy blocks egress to RustFS ([Finding 1](../../README.md#findings)). After the
extra policy was applied and the backup re-run:

| Event | Time | Since connections reached 0 |
|---|---|---|
| Retried backup completed | 15:09:12 | — |
| Sidecar first logged 0 connections | 15:09:26 | 0 |
| Sidecar: `setting hibernation on branch` | 15:14:30 | 5 min 4 s |
| Instance pod gone | 15:14:41 | 5 min 15 s |
| `psql` connects; gateway wakes the branch; first query returns | +15.4 s | — |

So a branch whose backups cannot complete never scales to zero, with no error on the branch itself.

**Clean run**, 2026-10-28, with the extra policy in place from the start: `lab-s2z` hibernated 367 s after its last query (about 5 minutes of inactivity plus the once-a-minute check), and the first query after that woke it in 16.0 s.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

`hols` 프로젝트의 자식 브랜치는 5분 동안 쓰지 않으면 hibernate됩니다(`up.sh`가
`--scale-to-zero-child true --inactivity-period-child 5`로 설정). `run.sh`는 다음 순서로 진행하며,
전체 약 7분이 걸립니다.
1. `base`의 자식을 만들고 쿼리를 한 번 실행합니다.
2. 그 브랜치의 Postgres pod가 사라질 때까지 지켜봅니다.
3. 다시 접속해서 첫 쿼리 시간을 잽니다.

### "비활성"을 판정하는 방법

모든 브랜치 pod에 붙는 sidecar(`services/scale-to-zero-sidecar/internal/sidecar/scale_to_zero.go`)가
1분마다 연결 수를 셉니다.

```sql
SELECT COUNT(*) FROM pg_stat_activity
WHERE state IN ('active', 'idle', 'idle in transaction')
  AND pg_backend_pid() != pg_stat_activity.pid AND usename != 'streaming_replica';
```

**idle 연결도 활동으로 셉니다.** 연결을 열어 둔 클라이언트가 하나라도 있으면 브랜치가 깨어 있습니다.
커넥션 풀, IDE, 잊고 둔 `psql`이 모두 해당합니다. 비활성 기간 동안 연결 수가 0이면 sidecar가 Branch
리소스에 hibernation을 설정하고 pod가 내려갑니다. 다음 접속이 오면 gateway가 브랜치를 깨웁니다.

### 결과

첫 실행 2026-10-28(M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0), 손자 브랜치 `g1`:

| 사건 | 시각 |
|---|---|
| 마지막 클라이언트 쿼리 | 14:54:21 |
| 10분 뒤에도 깨어 있음. sidecar가 1분마다 `open connections count: 1`을 기록 | — |
| 남아 있던 연결 하나: pod 시작부터 idle이던 `barman_cloud_backup` | — |

그 연결을 연 백업은 `ConnectTimeoutError … http://rustfs-eu:9000/backups/…`로 실패하고 있었습니다. 브랜치
NetworkPolicy가 RustFS로 가는 egress를 막기 때문입니다([발견 사항 1](../../README.md#발견-사항)). 추가
정책을 적용하고 백업을 다시 돌린 뒤의 결과입니다.

| 사건 | 시각 | 연결 0개가 된 뒤 |
|---|---|---|
| 재시도한 백업 완료 | 15:09:12 | — |
| sidecar가 처음으로 연결 0개를 기록 | 15:09:26 | 0 |
| sidecar: `setting hibernation on branch` | 15:14:30 | 5분 4초 |
| 인스턴스 pod 사라짐 | 15:14:41 | 5분 15초 |
| `psql` 접속 → gateway가 브랜치를 깨움 → 첫 쿼리 반환 | +15.4초 | — |

즉 백업을 끝내지 못하는 브랜치는 scale-to-zero가 영영 일어나지 않고, 브랜치 자체에는 아무 오류도 보이지 않습니다.

**클린 실행**, 2026-10-28(처음부터 추가 정책 적용): `lab-s2z`는 마지막 쿼리 후 367초에 hibernate됐습니다(비활성 5분과 1분 간격 검사를 합한 시간). 그 뒤 첫 쿼리로 16.0초 만에 깨어났습니다.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
