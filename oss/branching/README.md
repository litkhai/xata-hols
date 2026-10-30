# branching — copy, isolation, tree (S3–S5)

[English](#english) | [한국어](#한국어)

## English

A branch "starts as a copy of its parent" (CLI help). This lab checks that claim three ways, and
times how long the copy takes.

| Script | Scenario | Checks |
|---|---|---|
| `run.sh` | S3 | A child of `base` has the same `orders` row count and checksum (md5 over every `id, amount`) |
| | S4 | An update and a new table on the child are invisible on the parent, and a table on the parent is invisible on the child |
| | S5 | A grandchild inherits the child's changes, still answers after the child is deleted, and shows up as a root in `xata branch tree` |
| `measure-branch.sh <child> [parent]` | S3 timing | Seconds from `branch create` to: API reply, VolumeSnapshot `readyToUse`, child PVC `Bound`, branch healthy, first query |

### How a branch is made here

`branch create` → branch-operator takes a `VolumeSnapshot` of the parent's PVC → a new PVC is
restored from it → CNPG starts Postgres on it (`snapshot-recovery`). On this deployment the
snapshot class is `csi-hostpath-snapclass`, whose driver
([csi-driver-host-path](https://github.com/kubernetes-csi/csi-driver-host-path), `pkg/hostpath/controllerserver.go`)
creates the snapshot with `tar czf` and restores it with `tar zxvf`. **That is a full copy, so the
timings below measure that driver, not copy-on-write.** Xata's own clone path, the `XVolClone` restore type,
is not available in OSS. The operator enables it only when an `Xvol` API resource exists
(`services/branch-operator/operator.go:35,152`), and the repository ships nothing that provides one. It is
also used only for branches in a wakeup pool (`services/clusters/branchbuilder.go:126-141`).

### Results

Branch timing, first run 2026-10-28 (M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0,
`xata.micro`, parent `main` with a 1M- or 10M-row `orders` table; `branching/results/branch-size-2026-10-28.jsonl`):

| Parent size | API reply | Snapshot ready | PVC bound | Branch healthy | First query |
|---|---|---|---|---|---|
| 150 MB | 1.3 s | 12.9 s | 20.1 s | 52.7 s | 53.9 s |
| 1.4 GB | 1.2 s | 122.5 s | 267.0 s | 298.8 s | 300.5 s |

Ten times the data took about six times as long to reach healthy. Both the snapshot and the restore
grow with size, as a `tar` copy would. Roughly 30 s of each run is CNPG starting Postgres, independent of size.

Isolation and tree (S3–S5): every check passed in the first run and in `run.sh`.
- In the first run, deleting the middle branch left the grandchild's `parentID` pointing at the deleted id.
- `xata branch tree` then lists the grandchild as a root.

**Non-atomic snapshot.** In one run, a child created from `base` right after writes to it crash-looped with
`PANIC: could not locate a valid checkpoint record at 0/C008248`. A `tar` of a live data directory
is not a crash-consistent snapshot. `new_branch` (in `../platform/env.sh`) now runs `CHECKPOINT` on
the parent first. The `xata` role may run it, since it is a member of `pg_checkpoint`. `xata branch wait-ready` has no
timeout and waited on that branch indefinitely, so the scripts poll the branch status with a deadline.

**Clean run**, 2026-10-28: `measure-branch.sh lab-measure base` on the 50 MB `base` gave API 1.2 s, snapshot 4.7 s, PVC bound 6.0 s, healthy 35.4 s, first query 36.5 s. `run.sh`: 6/6 checks passed.

### Could CoW run here? (2026-10-30)

`cow-probe.sh` checks this. It needs Docker only, not a cluster. The run on 2026-10-30
(Docker Desktop, kernel 7.0.14-linuxkit, 12 CPU, 16 GiB; [output](results/cow-probe-2026-10-30.txt)) found:

- **No LVM-based CoW.** The kernel's device-mapper has only `crypt striped linear error`, and `dm_thin_pool` and
  `dm_snapshot` cannot be loaded. CSI drivers that snapshot with LVM, such as TopoLVM, need those targets.
- **btrfs and XFS reflink work**, but no CSI driver that snapshots with them was found (search, 2026-10-30).
- **What CoW would remove.** These are 1,400 random 1 MiB files (1.4 GB) on a loop filesystem, timed outside
  any cluster:

| Filesystem | `tar czf` + `tar zxf` (the hostpath driver's way) | `cp --reflink=always` |
|---|---|---|
| btrfs | 30.55 s | 0.03 s |
| XFS | 30.38 s | 0.03 s |

An earlier ad hoc run the same day gave 31.5 and 32.8 s against 0.04 s. The copy is only part of a branch's
time. In the cluster, 1.4 GB took 300.5 s to its first query, and even 50 MB took 36.5 s. So a CoW driver
would remove the part that grows with size, but not the fixed cost of scheduling the pods and starting
Postgres. Getting a real CoW branch timing needs a VM with a stock Linux kernel (for example, kind on Lima
with TopoLVM) or a test-only build of the hostpath driver that snapshots with `cp --reflink`. Neither was
done.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

CLI 도움말은 브랜치가 "부모의 복사본으로 시작한다"고 설명합니다. 이 실습은 그 주장을 세 가지 방법으로
확인하고, 복사에 걸리는 시간을 잽니다.

| 스크립트 | 시나리오 | 확인 내용 |
|---|---|---|
| `run.sh` | S3 | `base`의 자식이 같은 `orders` 행 수와 체크섬(모든 `id, amount`의 md5)을 가짐 |
| | S4 | 자식의 update와 새 테이블이 부모에서 보이지 않고, 부모의 테이블이 자식에서 보이지 않음 |
| | S5 | 손자가 자식의 변경을 물려받고, 자식을 지운 뒤에도 응답하며, `xata branch tree`에서 루트로 표시됨 |
| `measure-branch.sh <child> [parent]` | S3 시간 측정 | `branch create`부터 API 응답, VolumeSnapshot `readyToUse`, 자식 PVC `Bound`, 브랜치 healthy, 첫 쿼리까지의 초 |

### 여기서 브랜치가 만들어지는 과정

`branch create` → branch-operator가 부모 PVC의 `VolumeSnapshot`을 만듭니다 → 그 스냅샷에서 새 PVC를
복원합니다 → CNPG가 그 위에서 Postgres를 기동합니다(`snapshot-recovery`). 이 배포의 스냅샷 클래스는
`csi-hostpath-snapclass`입니다. 그 드라이버([csi-driver-host-path](https://github.com/kubernetes-csi/csi-driver-host-path),
`pkg/hostpath/controllerserver.go`)는 `tar czf`로 스냅샷을 만들고 `tar zxvf`로 복원합니다.
**즉 전체 복사이므로, 아래 시간은 copy-on-write가 아니라 이 드라이버를 잰 것입니다.** Xata 자체의
복제 경로인 `XVolClone` 복원 타입은 OSS에서 쓸 수 없습니다. operator는 `Xvol` API 리소스가 있을 때만
이 경로를 켜는데(`services/branch-operator/operator.go:35,152`), 저장소에는 그 리소스를 제공하는 것이 없습니다. 또
이 경로는 wakeup pool에 속한 브랜치에만 쓰입니다(`services/clusters/branchbuilder.go:126-141`).

### 결과

브랜치 시간, 첫 실행 2026-10-28. 조건은 M4 Pro, Docker 16 GiB, xata `f197e7b`, CLI 1.18.0, `xata.micro`,
부모 `main`의 `orders` 테이블 100만 행 또는 1,000만 행입니다(`branching/results/branch-size-2026-10-28.jsonl`).

| 부모 크기 | API 응답 | 스냅샷 완료 | PVC 바인딩 | 브랜치 healthy | 첫 쿼리 |
|---|---|---|---|---|---|
| 150 MB | 1.3초 | 12.9초 | 20.1초 | 52.7초 | 53.9초 |
| 1.4 GB | 1.2초 | 122.5초 | 267.0초 | 298.8초 | 300.5초 |

데이터가 10배가 되자 healthy까지 약 6배가 걸렸습니다. 스냅샷과 복원 모두 크기에 비례해서 늘었는데,
`tar` 복사라면 그래야 맞습니다. 각 실행의 약 30초는 CNPG가 Postgres를 기동하는 시간이고, 크기와 무관합니다.

격리와 트리(S3–S5)는 첫 실행과 `run.sh` 모두에서 전부 통과했습니다.
- 첫 실행에서 중간 브랜치를 지운 뒤에도 손자의 `parentID`는 삭제된 id를 가리켰습니다.
- 이후 `xata branch tree`는 손자를 루트로 표시합니다.

**원자적이지 않은 스냅샷.** 한 실행에서 쓰기 직후의 `base`로 만든 자식이
`PANIC: could not locate a valid checkpoint record at 0/C008248`로 재시작을 반복했습니다. 실행 중인 데이터
디렉터리를 `tar`로 묶으면 crash-consistent한 스냅샷이 되지 않습니다. 그래서 `new_branch`(`../platform/env.sh`)는
먼저 부모에서 `CHECKPOINT`를 실행합니다. `xata` 역할은 `pg_checkpoint` 멤버라 실행할 수 있습니다.
`xata branch wait-ready`에는 제한 시간이 없어 그 브랜치를 끝없이 기다렸기 때문에, 스크립트는 기한을 두고
브랜치 상태를 조회합니다.

**클린 실행**, 2026-10-28: 50 MB `base`에 `measure-branch.sh lab-measure base`를 돌린 결과는 API 1.2초, 스냅샷 4.7초, PVC 바인딩 6.0초, healthy 35.4초, 첫 쿼리 36.5초였습니다. `run.sh`는 6/6 확인을 통과했습니다.

### 여기서 CoW를 돌릴 수 있나(2026-10-30)

`cow-probe.sh`로 확인합니다. 클러스터 없이 Docker만 있으면 됩니다. 2026-10-30 실행 결과는 다음과
같습니다. 조건은 Docker Desktop, 커널 7.0.14-linuxkit, 12 CPU, 16 GiB이고, 출력은
[여기](results/cow-probe-2026-10-30.txt)에 있습니다.

- **LVM 기반 CoW는 안 됩니다.** 커널의 device-mapper에는 `crypt striped linear error`만 있고, `dm_thin_pool`과
  `dm_snapshot`은 불러올 수 없습니다. TopoLVM처럼 LVM으로 스냅샷을 만드는 CSI 드라이버에는 이 두 target이 필요합니다.
- **btrfs와 XFS reflink는 됩니다.** 다만 이것으로 스냅샷을 만드는 CSI 드라이버는 찾지 못했습니다(2026-10-30 검색).
- **CoW로 줄어드는 부분.** 무작위 1 MiB 파일 1,400개(1.4 GB)를 loop 파일시스템에 두고, 클러스터 밖에서 쟀습니다.

| 파일시스템 | `tar czf` + `tar zxf`(hostpath 드라이버 방식) | `cp --reflink=always` |
|---|---|---|
| btrfs | 30.55초 | 0.03초 |
| XFS | 30.38초 | 0.03초 |

같은 날 앞서 손으로 돌렸을 때는 31.5초와 32.8초 대 0.04초였습니다. 복사는 브랜치 시간의 일부일 뿐입니다.
클러스터에서 1.4 GB는 첫 쿼리까지 300.5초가 걸렸고, 50 MB도 36.5초가 걸렸습니다. 따라서 CoW 드라이버는
크기에 비례하는 부분은 없애지만, pod 스케줄링과 Postgres 기동에 드는 고정 시간은 남깁니다. 실제 CoW
브랜치 시간을 재려면 일반 Linux 커널 VM(예: Lima 위 kind + TopoLVM)이나, `cp --reflink`로 스냅샷을 만들도록
고친 시험용 hostpath 드라이버가 필요합니다. 둘 다 하지 않았습니다.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
