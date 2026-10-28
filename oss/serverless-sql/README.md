# serverless-sql — SQL over HTTP (S9)

[English](#english) | [한국어](#한국어)

## English

The gateway also answers `POST /sql` (`openapi/gateway.yaml`), for clients that cannot open a
Postgres connection (edge functions, browsers through a proxy, short-lived agents). Authentication
is the branch's own connection string in a `Connection-String` header. The control-plane API key is
not accepted there. The host in the request must match the host in the connection string. The
request and response shapes are those of Neon's serverless driver: `query` + `params`, or `queries`
for a batch in one transaction, with `Array-Mode`, `Raw-Text-Output` and `Batch-Isolation-Level` headers.

```bash
./query.sh base "select count(*) as users from users" -w ' HTTP %{http_code} %{time_total}s'
```

`query.sh` writes the header to a mode-600 file under `$XATA_HOLS_STATE` and passes it with
`curl -H @file`, so the password never appears in `ps` or shell history.

### Results

First run, 2026-10-28 (M4 Pro, Docker 16 GiB, xata `f197e7b`, gateway port-forward `:8443`, self-signed certificate):

| Request | Response |
|---|---|
| `select count(*) as users, max(id) as max_id from users` | HTTP 200 in 0.086 s; `fields` with type OIDs, `rows: [{"max_id": "100000", "users": "100000"}]`. `bigint` comes back as a string |
| `select * from no_such_table` | HTTP 400; `{"code": "42P01", "message": "relation \"no_such_table\" does not exist", "position": "15", …}` |

**Clean run**, 2026-10-28: `select count(*) as users from users` → HTTP 200 in 0.091 s, `rows: [{"users": "100000"}]`.

*Verified on xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28: a clean `platform/up.sh` + `run-all.sh` run ([log](../results/run-all-20261028T155333Z.txt)).*

## 한국어

gateway는 Postgres 연결을 열 수 없는 클라이언트를 위해 `POST /sql`(`openapi/gateway.yaml`)도 받습니다.
엣지 함수, 프록시를 거치는 브라우저, 수명이 짧은 에이전트 같은 경우입니다.
- **인증:** 브랜치 자체의 접속 문자열을 `Connection-String` 헤더에 넣습니다. control-plane API 키는 받지 않습니다.
- **호스트:** 요청의 호스트가 접속 문자열의 호스트와 같아야 합니다.
- **형식:** 요청과 응답은 Neon 서버리스 드라이버와 같은 형태입니다. `query`와 `params`를 보내거나,
  배치는 `queries`로 보내 한 트랜잭션에서 실행합니다. `Array-Mode`, `Raw-Text-Output`,
  `Batch-Isolation-Level` 헤더를 지원합니다.

```bash
./query.sh base "select count(*) as users from users" -w ' HTTP %{http_code} %{time_total}s'
```

`query.sh`는 헤더를 `$XATA_HOLS_STATE` 아래의 mode 600 파일에 쓰고 `curl -H @file`로 넘깁니다. 그래서
비밀번호가 `ps`나 셸 기록에 남지 않습니다.

### 결과

첫 실행 2026-10-28(M4 Pro, Docker 16 GiB, xata `f197e7b`, gateway port-forward `:8443`, 자체 서명 인증서):

| 요청 | 응답 |
|---|---|
| `select count(*) as users, max(id) as max_id from users` | 0.086초에 HTTP 200. 타입 OID가 담긴 `fields`와 `rows: [{"max_id": "100000", "users": "100000"}]`. `bigint`는 문자열로 옴 |
| `select * from no_such_table` | HTTP 400. `{"code": "42P01", "message": "relation \"no_such_table\" does not exist", "position": "15", …}` |

**클린 실행**, 2026-10-28: `select count(*) as users from users` → 0.091초에 HTTP 200, `rows: [{"users": "100000"}]`.

*검증: xata `f197e7b` · CLI 1.18.0 · PostgreSQL 18.6, 2026-10-28. 깨끗한 클러스터에서 `platform/up.sh`와 `run-all.sh`를 끝까지 실행했습니다([로그](../results/run-all-20261028T155333Z.txt)).*
