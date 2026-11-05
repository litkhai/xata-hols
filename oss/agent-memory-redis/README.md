# agent-memory-redis — agent memory on Xata (Postgres) vs Redis

[English](#english) | [한국어](#한국어)

## English

Can an agent keep its memory on Xata, which is plain Postgres, or does it still need Redis? This lab
runs the same LangGraph agent with its checkpoints and long-term memory on one or the other. It
also measures two places where Redis should be strongest: state that expires, and small state
rewritten every turn ([issue #10](https://github.com/litkhai/xata-hols/issues/10)).

### Results

Measured 2026-11-05 on an Apple M4 Pro with Docker Desktop at 16 GiB, on one kind node, after a fresh
`../platform/up.sh`. Versions: xata `f197e7b`, CLI 1.18.0, PostgreSQL 18.6, Redis 8.10.3, LangGraph
1.2.20 (langgraph-checkpoint-postgres 3.2.0, langgraph-checkpoint-redis 0.6.0). The SQL gateway ran at
its chart's resources (Local deviations, below). Raw lines are in [`results/`](results/). Runs that were
replaced, and why, are in [`results/superseded/`](results/superseded/).

**Short answer**

- **With a real agent's think time, the two are the same.** At 1–3 s per turn both did 22 turns/s at
  C=50. The persistence work took about 10 ms of each 2-second turn. Speed is not what decides here.
- **At saturation, durability decides.**
  - With an fsync on every commit (*strict*), Postgres is ahead from C=10: 598 vs 383 turns/s, and
    612 vs 524 at C=50. Postgres commits a group of transactions with one flush. Redis fsyncs inside
    its one event loop, and every other command waits for it, memory search included: p50 61 ms at
    C=50 on Redis, 5 ms on Postgres.
  - With *relaxed* durability, Redis is ahead: 966 vs 670 turns/s at C=10, and 1,150 vs 723 at C=50,
    where the client was the limit.
- **Redis keeps everything in RAM, and LangGraph's Redis integration is heavy.**
  - A finished 16-turn session holds 436 KiB of Redis RAM, against 149 KiB of Postgres disk.
  - The 50,000 memories take 325 MiB of RAM, against 185 MiB of disk. `AsyncRedisStore` always
    stores vectors as JSON text (`store/redis/aio.py`, `_vector_storage_type = "json"`).
  - With `maxmemory 900mb` and the memories loaded, about 1,350 finished sessions fit before
    `noeviction` refuses writes. On Redis, a retention TTL is not optional.
- **Expiry is simpler on Redis.**
  - Redis keys were gone within 0.4 s of their TTL, and the memory came back.
  - On Postgres, a `DELETE` job leaves the table at full size until autovacuum. At 100,000 rows it was
    still 268 MiB afterwards.
  - Time partitions give the space back at once, but rows go in steps of the partition width.
- **Rewriting one growing `jsonb` row is the costly pattern on Postgres.**
  - The WAL written by one append grows from 1.0 KiB at turn 1 to 11.4 KiB at turn 16.
  - With one row per message it stays at about 0.85 KiB, close to Redis's 0.66 KiB of AOF.
- **`kill -9`.**
  - At strict, neither side lost an acknowledged write.
  - At relaxed, Postgres lost 38 of 10,529 (`synchronous_commit=off`) and Redis none. Redis writes
    its AOF buffer to the file before it replies (`src/server.c`, `beforeSleep`), so killing the
    process loses nothing. The only exception is a write postponed while an earlier fsync is still
    running, for up to 2 s (`src/aof.c`, `flushAppendOnlyFile`). A power loss is a different test.

**Also found**

- **Xata's default connection string goes straight to Postgres.** `xata branch url` prints a
  `<branch>` hostname, and the gateway sends that to the Postgres primary. Only `<branch>-pooler`
  reaches PgBouncer (`services/gateway/session/proxy.go`, `resolver.go` `parseEndpoint`).
  - `xata.micro` has `max_connections = 50`. The first attempt gave each of 6 client processes up to
    11 connections, and at C=50 it logged 195 connection errors: 180 `remaining connection slots are
    reserved` and 15 `too many clients` ([log](results/superseded/sessions-first-attempt-stderr.txt)).
    This lab caps the client at 36 connections.
  - PgBouncer's `default_pool_size` is 45 of those 50. After a busy pooler run, a direct connection
    failed until PgBouncer's idle server connections timed out (60 s). `run.sh` ends them before
    each run.
- **The local platform's gateway was the bottleneck at first.** At the local overlay's 200m CPU,
  Postgres stopped at 215 turns/s (strict, C=10). The gateway pod had been throttled 1,493 times,
  37 s in all (`cpu.stat`). At the chart's resources, the same run did 598 turns/s with no throttling
  ([superseded run](results/superseded/sessions-20261105T080500Z-gateway-200m.jsonl)).
- **On Redis, a `FLUSHDB` on any database drops every search index.** That includes LangGraph's
  indexes over db 0: RediSearch 8.10.1 frees all indexes on any `FLUSHDB` (`src/indexes.c`,
  `onFlush`). The data stays, but searches fail until the app runs `setup()` again. Tested here
  2026-11-05 with a `FLUSHDB` on db 1.
- **Redis rebuilds vector indexes when it loads.** Its log says `rebuild-from-keyspace`. A reload with
  the 50,000-vector index took 6.2 s; without the index it took 0.8 s. Postgres's crash recovery
  after `kill -9` replayed WAL in 0.54 s. Its HNSW index lives on disk.
- **The LangGraph client costs about as much as the databases.** It spent 4.6–7.8 ms of CPU per turn.
  At C=1 one client process was the limit.

#### Notes on the tables

- **Sessions.** Every run had 0 failed sessions.
  - Server CPU is the average over the run, start-up included. At C ≥ 10, Postgres used 1.2–1.7 of
    its 2 CPUs. Redis used 0.3–0.8 cores, because the event loop is single-threaded and, at strict,
    waits on fsync.
  - The gateway used up to 0.7 cores and was never throttled.
  - The gateway-pooler path is 5–10% slower than the gateway's default path. It adds a hop, and it
    gains nothing while the client holds at most 36 connections.
- **Footprint.** Postgres counts tables and indexes on disk, after `VACUUM FULL`. Redis counts
  `used_memory`, after an AOF rewrite. For Redis, `MEMORY USAGE` overstates JSON documents (its sum
  exceeds `used_memory`), so the table gives shares only.
- **`kill -9`.** "Seconds until writable" is mostly kubelet's restart back-off: each server was
  killed four times in this session. The engines' own logs show the recovery itself:
  - Postgres: redo 0.54 s, ready 0.83 s after start.
  - Redis: AOF load 2.0 s (strict run) and 9.3 s (relaxed run).
- **Expiry.**
  - pg_cron is not in `shared_preload_libraries` on a branch, so the script ran the job every 10 s.
  - WAL is counted for the whole server. The first 10,000-row partition run read 11.4 MiB because
    of background activity; the repeat, in the table, read 0.34 MiB.
  - Autovacuum shrank the 10,000-row table to 0.8 MiB. It left the 100,000-row table at 267.6 MiB:
    the space can be reused inside the table but went back to no one.
- **Small state.** One client process ran all workers, and the runs marked client-bound measure
  Python more than the server. Compare latency and bytes, not turns/s.
  - At C=50, the `jsonb` variant left 2,450 dead tuples (97% HOT updates) and a 70 MiB table.
    One row per message left a 12 MiB table and no dead tuples.

#### Recall (preloaded 50,000 memories, 500 queries, per-user filter)

| System | recall@5 | recall@10 | search p50 ms | p99 ms |
|---|---|---|---|---|
| postgres | 1.0 | 1.0 | 1.7 | 3.3 |
| redis | 1.0 | 1.0 | 0.9 | 2.0 |

#### Sessions, think time 0 (saturation)

| Durability | C | Path | Sessions | Errors | Turns/s | Turn overhead p50 ms | p99 | Checkpoint put p50 ms | p99 | Memory search p50 ms | p99 | Server CPU cores | Gateway cores (throttled) | Client CPU ms/turn | Client-bound |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| relaxed | 1 | gateway | 30 | 0 | 180.15 | 4.4 | 15.0 | 1.1 | 2.5 | 2.1 | 3.6 | 0.46 | 0.13 (0.0 s) | 4.93 |  |
| relaxed | 1 | gateway-pooler | 30 | 0 | 172.94 | 4.7 | 11.6 | 1.3 | 2.6 | 2.2 | 3.6 | 0.46 | 0.13 (0.0 s) | 4.92 |  |
| relaxed | 1 | redis | 30 | 0 | 239.34 | 4.0 | 8.8 | 0.8 | 1.7 | 1.2 | 2.4 | 0.22 | – | 4.64 | yes |
| relaxed | 10 | gateway | 100 | 0 | 669.69 | 12.7 | 39.6 | 3.1 | 8.6 | 3.2 | 9.8 | 1.42 | 0.51 (0.0 s) | 7.66 |  |
| relaxed | 10 | gateway-pooler | 100 | 0 | 612.38 | 14.1 | 39.4 | 3.5 | 9.6 | 3.4 | 15.0 | 1.33 | 0.43 (0.0 s) | 7.62 |  |
| relaxed | 10 | redis | 100 | 0 | 966.46 | 9.0 | 19.4 | 1.3 | 3.6 | 2.8 | 7.6 | 0.57 | – | 6.19 |  |
| relaxed | 50 | gateway | 500 | 0 | 722.73 | 62.2 | 113.8 | 15.6 | 34.7 | 5.1 | 29.8 | 1.74 | 0.69 (0.0 s) | 6.38 |  |
| relaxed | 50 | gateway-pooler | 500 | 0 | 647.59 | 69.6 | 147.5 | 17.4 | 43.3 | 7.3 | 41.1 | 1.59 | 0.58 (0.0 s) | 6.54 |  |
| relaxed | 50 | redis | 500 | 0 | 1149.77 | 38.5 | 81.7 | 2.7 | 12.1 | 21.2 | 52.7 | 0.82 | – | 5.01 | yes |
| strict | 1 | gateway | 30 | 0 | 173.12 | 4.8 | 14.3 | 1.3 | 2.6 | 1.9 | 3.1 | 0.28 | 0.11 (0.0 s) | 4.74 |  |
| strict | 1 | gateway-pooler | 30 | 0 | 164.26 | 5.1 | 14.1 | 1.4 | 3.0 | 2.1 | 3.5 | 0.48 | 0.11 (0.0 s) | 4.84 |  |
| strict | 1 | redis | 30 | 0 | 153.93 | 6.0 | 12.1 | 1.5 | 3.0 | 2.1 | 4.5 | 0.16 | – | 4.92 |  |
| strict | 10 | gateway | 100 | 0 | 598.29 | 15.1 | 26.6 | 3.7 | 8.8 | 3.2 | 7.1 | 1.21 | 0.46 (0.0 s) | 7.44 |  |
| strict | 10 | gateway-pooler | 100 | 0 | 555.86 | 15.9 | 34.6 | 4.1 | 9.6 | 3.2 | 10.1 | 1.19 | 0.43 (0.0 s) | 7.43 |  |
| strict | 10 | redis | 100 | 0 | 382.65 | 21.6 | 55.4 | 3.2 | 9.5 | 9.4 | 29.0 | 0.34 | – | 7.12 |  |
| strict | 50 | gateway | 500 | 0 | 612.34 | 74.6 | 142.9 | 18.9 | 43.4 | 5.0 | 26.0 | 1.66 | 0.6 (0.0 s) | 6.56 |  |
| strict | 50 | gateway-pooler | 500 | 0 | 581.65 | 78.9 | 154.4 | 20.1 | 47.1 | 5.9 | 33.0 | 1.52 | 0.53 (0.0 s) | 6.56 |  |
| strict | 50 | redis | 500 | 0 | 523.69 | 86.9 | 179.4 | 4.0 | 12.7 | 61.1 | 110.5 | 0.49 | – | 5.46 |  |

#### Sessions, think time 1–3 s

| Durability | C | Path | Sessions | Errors | Turns/s | Turn overhead p50 ms | p99 | Checkpoint put p50 ms | p99 | Memory search p50 ms | p99 | Server CPU cores | Gateway cores (throttled) | Client CPU ms/turn | Client-bound |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| relaxed | 1 | gateway | 2 | 0 | 0.49 | 12.4 | 19.3 | 1.4 | 3.4 | 3.1 | 6.0 | 0.42 | 0.0 (0.0 s) | 15.94 |  |
| relaxed | 1 | redis | 2 | 0 | 0.49 | 11.9 | 20.9 | 1.2 | 5.6 | 2.1 | 5.9 | 0.01 | – | 16.78 |  |
| relaxed | 10 | gateway | 20 | 0 | 4.56 | 12.9 | 36.2 | 1.8 | 6.5 | 3.7 | 8.2 | 0.16 | 0.01 (0.0 s) | 15.9 |  |
| relaxed | 10 | redis | 20 | 0 | 4.56 | 12.6 | 22.2 | 1.5 | 5.1 | 2.5 | 6.3 | 0.02 | – | 15.97 |  |
| relaxed | 50 | gateway | 100 | 0 | 21.8 | 8.9 | 42.9 | 1.3 | 19.6 | 3.1 | 23.8 | 0.13 | 0.03 (0.0 s) | 8.3 |  |
| relaxed | 50 | redis | 100 | 0 | 22.14 | 8.9 | 39.2 | 1.2 | 7.2 | 2.0 | 24.6 | 0.04 | – | 8.71 |  |
| strict | 1 | gateway | 2 | 0 | 0.49 | 17.3 | 60.7 | 2.5 | 16.7 | 3.8 | 15.2 | 0.09 | 0.0 (0.0 s) | 18.04 |  |
| strict | 1 | redis | 2 | 0 | 0.49 | 21.3 | 111.8 | 3.2 | 10.3 | 4.2 | 7.4 | 0.01 | – | 20.11 |  |
| strict | 10 | gateway | 20 | 0 | 4.55 | 13.6 | 33.0 | 2.2 | 6.3 | 3.4 | 9.3 | 0.04 | 0.01 (0.0 s) | 16.3 |  |
| strict | 10 | redis | 20 | 0 | 4.55 | 15.3 | 29.3 | 2.5 | 6.9 | 3.1 | 12.7 | 0.02 | – | 15.53 |  |
| strict | 50 | gateway | 100 | 0 | 21.87 | 9.1 | 52.9 | 1.6 | 54.2 | 2.8 | 30.3 | 0.15 | 0.03 (0.0 s) | 8.17 |  |
| strict | 50 | redis | 100 | 0 | 22.0 | 11.0 | 52.8 | 2.0 | 9.3 | 2.6 | 32.4 | 0.04 | – | 8.26 |  |

#### Bytes held per finished session (100 sessions x 16 turns, compacted start)

| System | Where | KiB per session | Sessions per GiB | Largest parts |
|---|---|---|---|---|
| postgres | disk (tables + indexes) | 149.4 | 7,016 | checkpoint_blobs 60.0 KiB, checkpoints 48.2 KiB, checkpoint_writes 35.8 KiB, store_vectors 5.5 KiB |
| redis | RAM (used_memory) | 435.5 | 2,407 | checkpoint 86%, checkpoint_write 7%, write_keys_zset 4%, store_vectors 2% |

#### kill -9 under load (10 writers, think time 0)

| Durability | System | Acked checkpoints | Lost | Writes/s | Seconds until writable | Note |
|---|---|---|---|---|---|---|
| relaxed | postgres | 10529 | 38 | 636.3 | 48.12 |  |
| relaxed | redis | 8833 | 0 | 564.1 | 61.63 |  |
| strict | postgres | 9704 | 0 | 583.7 | 24.58 |  |
| strict | redis | 10107 | 0 | 648.5 | 30.04 |  |

#### Expiry (2 KiB random payload, TTL 60 s)

| Sessions | Variant | Expiry by | Write s | MiB while live | s after expiry until gone | WAL/AOF MiB for expiry | MiB left when gone | Dead tuples when gone | s until autovacuum | MiB after autovacuum |
|---|---|---|---|---|---|---|---|---|---|---|
| 10,000 | pg_delete | script every 10s | 0.85 | 27.5 | 1.1 | 2.02 | 27.5 | 10000 | 40.1 | 0.8 |
| 10,000 | pg_partition | script every 10s | 0.91 | 27.5 | 11.3 | 0.34 | 0.2 | – | – | – |
| 10,000 | redis_ttl | TTL | 2.28 | 25.2 | 0.4 | 0.27 | 0.5 | – | – | – |
| 100,000 | pg_delete | script every 10s | 7.95 | 274.1 | 8.3 | 19.98 | 274.2 | 100000 | 15.0 | 267.6 |
| 100,000 | pg_partition | script every 10s | 7.62 | 271.6 | 18.4 | 2.21 | 0.2 | – | – | – |
| 100,000 | redis_ttl | TTL | 9.3 | 250.9 | 0.4 | 2.84 | 0.5 | – | – | – |

#### Small state rewritten every turn (16 turns, 600-char messages)

| C | Variant | Turns | Turns/s | Read+append p50 ms | p99 | WAL/AOF bytes per turn | Table MiB | Dead tuples | HOT share | Autovacuum runs | Client-bound |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | pg_jsonb | 3200 | 5716.1 | 1.4 | 7.9 | 6326 | 17.1 | 475 | 0.972 | 0 | yes |
| 10 | pg_rows | 3200 | 6284.4 | 1.2 | 14.0 | 874 | 2.5 | 0 | – | 0 | yes |
| 10 | redis_list | 3200 | 4996.7 | 1.8 | 5.5 | 681 | – | – | – | – |  |
| 50 | pg_jsonb | 16000 | 5682.7 | 6.1 | 37.8 | 6422 | 70.2 | 2450 | 0.968 | 1 |  |
| 50 | pg_rows | 16000 | 3957.6 | 7.6 | 81.1 | 874 | 12.4 | 0 | – | 0 |  |
| 50 | redis_list | 16000 | 8810.9 | 5.0 | 29.7 | 681 | – | – | – | – | yes |

#### The preloaded long-term memories (1,000 users x 50, 384 dimensions)

| System | Memories | Where | MiB | KiB per memory | Detail (MiB) |
|---|---|---|---|---|---|
| postgres | 50000 | disk, after VACUUM FULL | 184.6 | 3.78 | store_vectors heap 78.1 + indexes 99.6; store 6.8 |
| redis | 50000 | RAM, after a restart from the AOF | 324.5 | 6.65 | used_memory_dataset 319.1 |

#### Bytes written by one append, by turn (single session)

| Variant | turn 1 | turn 2 | turn 4 | turn 8 | turn 12 | turn 16 |
|---|---|---|---|---|---|---|
| pg_jsonb | 1032 | 1480 | 3200 | 5976 | 8768 | 11664 |
| pg_rows | 928 | 832 | 832 | 832 | 976 | 856 |
| redis_list | 673 | 673 | 673 | 673 | 673 | 673 |

### What runs

Everything runs in the kind cluster from [`../platform`](../platform/). An empty branch,
`agentmem` (`xata.micro`, scale-to-zero off), holds the Postgres side. A Redis pod in the same
cluster holds the Redis side. A bench pod runs the client, so both systems are reached over the
same cluster network.

| | Postgres (Xata branch) | Redis |
|---|---|---|
| Engine | PostgreSQL 18.6, pgvector 0.8.7 | Redis Open Source 8.10.3 (Query Engine and JSON bundled) |
| Limits | Postgres 924 Mi, 2 CPU; PgBouncer 100 Mi, 0.5 CPU (Xata's `xata.micro`) | 1 GiB, 2 CPU; `maxmemory 900mb`, `noeviction` |
| LangGraph checkpointer | `AsyncPostgresSaver` (langgraph-checkpoint-postgres 3.2.0) | `AsyncRedisSaver` (langgraph-checkpoint-redis 0.6.0) |
| LangGraph store | `AsyncPostgresStore`, pgvector HNSW `m=16, ef_construction=64`, `ef_search` 40 | `AsyncRedisStore`, HNSW `m=16, ef_construction=64, ef_runtime=40` |
| Paths | **gateway**: the connection string `xata branch url` prints, client → SQL gateway (TLS) → Postgres; **gateway-pooler**: the same with `<branch>-pooler` as the host's first label, client → gateway → PgBouncer (transaction mode) → Postgres | client → Redis |

**The agent** ([`bench/agentmem.py`](bench/agentmem.py)) is a one-node LangGraph graph. The "model"
sleeps a think time and answers 600 fixed characters, so no LLM is called. One session:

1. Load the thread state.
2. Run 16 turns. Each turn recalls 5 memories of the session's user (`store.asearch`, filtered to
   the user's namespace), waits the think time, and LangGraph writes its checkpoints.
3. Every 4th turn writes a new long-term memory with its embedding.

Before the runs, each store gets 1,000 users × 50 memories. The embeddings are synthetic and seeded
([`bench/vectors.py`](bench/vectors.py)): 384 dimensions, 64 topic centroids plus noise. No model or
API is called, so ground truth for recall can be computed exactly.

**The matrix** for sessions: 2 durability pairs × 2 think-time modes × concurrency 1 / 10 / 50.
Each configuration runs on the gateway path and on Redis. With think time 0, Postgres also runs on
the gateway-pooler path. That makes 30 runs. Each client process holds at most 6 Postgres connections,
36 in all, because the gateway path goes straight to Postgres and `xata.micro` has
`max_connections = 50`. Redis clients are not capped.

- **Durability pairs.** *strict*: Redis `appendfsync always` with Postgres `synchronous_commit=on`.
  *relaxed*: `everysec` with `off`. Redis keeps AOF only (RDB snapshots off).
- **Think time 0** measures saturation: per-operation p50/p99 and turns per second. At C=1 a run has
  30 sessions; otherwise 10 per worker.
- **Think time 1–3 s** (uniform) is closer to a real agent. Each worker runs 2 sessions.

**Expiry** ([`bench/expiry.py`](bench/expiry.py)): 10,000 and 100,000 sessions of 2 KiB random
payload, TTL 60 s, strict durability.
- `redis_ttl`: `SET … EX 60`.
- `pg_delete`: a table and `DELETE … WHERE expires_at < now()` every 10 s. pg_cron is not in
  `shared_preload_libraries` on the branch, so the script runs the same statement on that schedule.
- `pg_partition`: time partitions 10 s wide. Each partition is dropped once its upper bound has passed,
  which is what pg_partman's retention does (pg_partman 5.4.1 is available but not used).

**Small state rewritten every turn** ([`bench/hotstate.py`](bench/hotstate.py)): a session's message
list, read and appended once per turn for 16 turns. Messages are 600 characters of random
pseudo-words. Strict durability, concurrency 10 and 50, 20 sessions per worker. Postgres runs on the
gateway-pooler path, and each worker holds one connection for its sessions.
- `redis_list`: `LRANGE` + `RPUSH`.
- `pg_jsonb`: one `jsonb` row per session, rewritten each turn (the common pattern).
- `pg_rows`: one row per message (the idiomatic design).

A single-session probe records the WAL or AOF bytes of each turn's append.

**Crash** ([`run.sh kill`](run.sh)): 10 writers with think time 0. After 15 s the server process
gets `SIGKILL`: `redis-server` from the kind node, or the postmaster inside the branch pod. At the
first error every writer stops. The client then waits until the server accepts writes again and
checks that every checkpoint acknowledged before the error is still there.

**Sizes.** `setup` also measures one finished session's footprint: 100 sessions from a compacted
start (`VACUUM FULL` on Postgres, an AOF rewrite on Redis). `memsize` measures the 50,000 preloaded
memories, on Postgres after `VACUUM FULL` and on Redis after a restart from its AOF.

### Fairness rules

- Same graph, same data, same embeddings. Only the checkpointer and the store change.
- Both checkpointers keep full history (`AsyncRedisSaver`, not `ShallowRedisSaver`).
- HNSW on both sides with the same `m` and `ef_construction`. `AsyncRedisStore` defaults to a FLAT
  (brute-force) index and redisvl's HNSW defaults differ (`ef_construction` 200, `ef_runtime` 10).
  The lab sets pgvector's values on Redis (`bench/agentmem.py`, `REDIS_INDEX`). Recall is measured
  before the latency runs.
- Durability is set in pairs. Postgres is set on the role (`ALTER ROLE … SET synchronous_commit`),
  because the branch's PgBouncer runs in transaction mode, where a session `SET` does not stick. Then
  every pooled connection is checked (`bench/ctl.py pg-sync`).
- Redis AOF auto-rewrite is off during a run. The lab compacts the AOF between runs.
- Before each Postgres run, the role's idle backends are ended (`bench/ctl.py pg-idle-reset`), so
  PgBouncer's idle server connections from an earlier run do not take Postgres's slots.
- Throughput counts the window in which turns ran, without process start-up and connecting.
- Sessions clean up after themselves (`adelete_thread`, store deletes), so each run starts from the
  same preloaded state.
- The client runs in 6 processes. A run whose client CPU reaches 85% of them is marked client-bound.

### Run it

```bash
oss/platform/up.sh                 # once
oss/agent-memory-redis/run.sh      # up, setup, sessions, kill, expiry, hotstate, memsize: about 45 minutes
oss/agent-memory-redis/report.py   # tables from results/*.jsonl
oss/agent-memory-redis/run.sh down # remove Redis, the bench pod and the agentmem branch
```

Steps run on their own too: `run.sh up | setup | sessions | kill | expiry | hotstate | memsize`, and one
session configuration with `run.sh one <gateway|gateway_pooler> <postgres|redis> <label> <0|real> <C> <note>`.
This lab is not part of `oss/run-all.sh`, because of its run time.

### Local deviations

- **The SQL gateway runs at its chart's resources during this lab.** Upstream's local overlay
  shrinks it to fit a laptop: `GOMAXPROCS=1`, CPU limit 200m (`kustomize/overlays/local/kustomization.yaml`,
  the `gateway` patch). The gateway chart ships `GOMAXPROCS=8`, CPU limit 8 and request 4
  (`charts/gateway/values.yaml`, `templates/deployment.yaml`). At 200m the gateway, not Postgres,
  set every Postgres number (Also found, above). `run.sh up` applies the chart's values and
  `run.sh down` puts the overlay's back.
- The bench pod connects to the gateway Service's cluster IP and keeps the branch hostname for TLS
  SNI (`hostaddr=` in the connection string). From inside the cluster, `*.127.0.0.1.nip.io` resolves
  to the pod itself. The platform itself is unchanged beyond the root README's list.

### Limits

- One laptop: kind on Docker Desktop with 16 GiB, and every pod on one node. The platform takes
  about 9 GiB of memory requests. Numbers are for comparing the two systems under the same
  conditions, not for sizing.
- Postgres "bytes per session" is disk; Redis "bytes per session" is RAM. That difference is the
  point, but they are not the same resource.
- OSS only: no Xata Cloud, no Redis Cloud, no ClickHouse.
- This lab does not time branches. Branch timings on this machine are not CoW numbers (root README, Finding 4).
- **Redis strengths not measured here:**
  - task queues (Streams consumer groups, `XAUTOCLAIM`)
  - locks with expiry and rate limits (`SET NX PX`, `INCR`, Lua)
  - pub/sub for streaming progress
  - semantic cache with eviction
  - connection fan-out beyond 50 clients

## 한국어

에이전트가 메모리를 Xata, 즉 일반 Postgres에 두면 충분할까요, 아니면 Redis가 여전히 필요할까요? 이 실습은
같은 LangGraph 에이전트의 체크포인트와 장기 메모리를 둘 중 한쪽에 두고 돌립니다. Redis가 가장 강할 만한
두 곳도 잽니다. 만료되는 상태와, 매 턴 고쳐 쓰는 작은 상태입니다([이슈 #10](https://github.com/litkhai/xata-hols/issues/10)).

### 결과

2026-11-05에 쟀습니다. 조건은 Apple M4 Pro, Docker Desktop 16 GiB, kind 노드 1개이고, `../platform/up.sh`로
새로 띄운 직후입니다. 버전은 xata `f197e7b`, CLI 1.18.0, PostgreSQL 18.6, Redis 8.10.3, LangGraph 1.2.20
(langgraph-checkpoint-postgres 3.2.0, langgraph-checkpoint-redis 0.6.0)입니다. SQL gateway는 chart의 리소스로
돌렸습니다(아래 로컬 조정). 원본 기록은 [`results/`](results/)에 있습니다. 다시 잰 실행과 그 이유는
[`results/superseded/`](results/superseded/)에 있습니다.

**짧은 답**

- **실제 에이전트처럼 think time이 있으면 둘은 같습니다.** 턴마다 1–3초를 쉬면 C=50에서 둘 다 22턴/초였습니다.
  저장에 쓴 시간은 2초짜리 턴에서 약 10 ms였습니다. 여기서는 속도가 선택 기준이 되지 않습니다.
- **포화 상태에서는 내구성 설정이 결과를 가릅니다.**
  - 커밋마다 fsync하면(*strict*) C=10부터 Postgres가 앞섭니다. C=10에서 598 대 383턴/초, C=50에서 612 대
    524였습니다. Postgres는 트랜잭션 여러 개를 flush 한 번으로 커밋합니다. Redis는 하나뿐인 이벤트 루프 안에서
    fsync하고, 그동안 다른 명령은 모두 기다립니다. 메모리 검색도 기다려서, C=50에서 검색 p50이 Redis 61 ms,
    Postgres 5 ms였습니다.
  - 내구성을 낮추면(*relaxed*) Redis가 앞섭니다. C=10에서 966 대 670턴/초, C=50에서 1,150 대 723이었습니다.
    C=50에서는 클라이언트가 한계였습니다.
- **Redis는 모든 것을 RAM에 두고, LangGraph의 Redis 통합은 무겁습니다.**
  - 16턴짜리 세션이 끝나면 Redis RAM 436 KiB를 차지합니다. Postgres는 디스크 149 KiB입니다.
  - 메모리 5만 건은 RAM 325 MiB를 차지합니다. Postgres는 디스크 185 MiB입니다. `AsyncRedisStore`는 벡터를
    항상 JSON 텍스트로 저장합니다(`store/redis/aio.py`, `_vector_storage_type = "json"`).
  - `maxmemory 900mb`에 메모리를 넣어 둔 상태라면, 끝난 세션 약 1,350개를 넣으면 `noeviction`이 쓰기를
    거부합니다. Redis에서는 보존 TTL이 선택이 아닙니다.
- **만료는 Redis가 단순합니다.**
  - Redis 키는 TTL이 지나고 0.4초 안에 사라졌고, 메모리도 돌아왔습니다.
  - Postgres에서 `DELETE` 작업을 돌리면 autovacuum 전까지 테이블 크기가 그대로입니다. 10만 행일 때는
    autovacuum 뒤에도 268 MiB였습니다.
  - 시간 파티션은 공간을 바로 돌려주지만, 행은 파티션 폭 단위로 계단식으로 사라집니다.
- **커지는 `jsonb` 행 하나를 통째로 다시 쓰는 것이 Postgres에서 비싼 패턴입니다.**
  - 덧붙이기 한 번에 쓰는 WAL이 1턴 1.0 KiB에서 16턴 11.4 KiB로 늘어납니다.
  - 메시지당 한 행으로 두면 약 0.85 KiB로 일정하고, Redis AOF 0.66 KiB와 비슷합니다.
- **`kill -9`.**
  - strict에서는 양쪽 모두 ack한 쓰기를 하나도 잃지 않았습니다.
  - relaxed에서는 Postgres가 10,529개 중 38개를 잃었고(`synchronous_commit=off`), Redis는 0개였습니다.
    Redis는 응답하기 전에 AOF 버퍼를 파일에 씁니다(`src/server.c`, `beforeSleep`). 그래서 프로세스를 죽여도
    잃는 것이 없습니다. 예외는 이전 fsync가 아직 진행 중이라 쓰기가 최대 2초 미뤄진 경우뿐입니다(`src/aof.c`,
    `flushAppendOnlyFile`). 정전은 다른 시험입니다.

**함께 찾은 것**

- **Xata의 기본 연결 문자열은 Postgres로 바로 갑니다.** `xata branch url`은 `<branch>` 호스트명을 주고,
  gateway는 이를 Postgres primary로 보냅니다. `<branch>-pooler`일 때만 PgBouncer로 갑니다
  (`services/gateway/session/proxy.go`, `resolver.go`의 `parseEndpoint`).
  - `xata.micro`는 `max_connections = 50`입니다. 첫 시도에서는 클라이언트 프로세스 6개가 각자 연결을 최대
    11개 잡았고, C=50에서 연결 오류가 195번 났습니다. `remaining connection slots are reserved`가 180번,
    `too many clients`가 15번입니다([로그](results/superseded/sessions-first-attempt-stderr.txt)). 이 실습은
    클라이언트 연결을 36개로 제한합니다.
  - PgBouncer의 `default_pool_size`는 그 50개 중 45개입니다. pooler를 많이 쓴 실행 뒤에는, PgBouncer의 idle
    서버 연결이 시간 초과(60초)로 닫힐 때까지 직접 연결이 실패했습니다. `run.sh`는 실행마다 그 연결을 먼저 끊습니다.
- **처음에는 로컬 플랫폼의 gateway가 병목이었습니다.** 로컬 overlay의 CPU 200m에서는 Postgres가 strict, C=10에서
  215턴/초에 멈췄습니다. gateway pod는 1,493번, 모두 37초 throttling됐습니다(`cpu.stat`). chart 리소스로 올리자
  같은 실행이 throttling 없이 598턴/초였습니다([다시 잰 실행](results/superseded/sessions-20261105T080500Z-gateway-200m.jsonl)).
- **Redis에서는 어느 db에서든 `FLUSHDB`를 하면 검색 인덱스가 모두 사라집니다.** db 0을 대상으로 한 LangGraph의
  인덱스도 포함입니다. RediSearch 8.10.1은 `FLUSHDB`가 시작되면 모든 인덱스를 해제합니다(`src/indexes.c`의
  `onFlush`). 데이터는 남지만, 앱이 `setup()`을 다시 부르기 전까지 검색이 실패합니다. 2026-11-05에 db 1의
  `FLUSHDB`로 직접 확인했습니다.
- **Redis는 로드할 때 벡터 인덱스를 다시 만듭니다.** 로그에 `rebuild-from-keyspace`라고 나옵니다. 벡터 5만 개
  인덱스가 있을 때 재로드에 6.2초, 인덱스가 없을 때 0.8초가 걸렸습니다. Postgres는 `kill -9` 뒤 crash recovery에서
  WAL을 0.54초 동안 재생했습니다. HNSW 인덱스가 디스크에 있기 때문입니다.
- **LangGraph 클라이언트도 DB만큼 비용이 듭니다.** 턴마다 CPU를 4.6–7.8 ms 썼습니다. C=1에서는 클라이언트
  프로세스 하나가 한계였습니다.

#### 표 읽는 법

- **세션.** 모든 실행에서 실패한 세션은 0개였습니다.
  - 서버 CPU는 시작 시간을 포함한 실행 전체의 평균입니다. C ≥ 10에서 Postgres는 CPU 2개 중 1.2–1.7개를 썼습니다.
    Redis는 0.3–0.8코어를 썼습니다. 이벤트 루프가 스레드 하나이고, strict에서는 fsync를 기다리기 때문입니다.
  - gateway는 최대 0.7코어를 썼고, throttling은 없었습니다.
  - gateway-pooler 경로는 gateway 기본 경로보다 5–10% 느립니다. 한 단계를 더 거치고, 클라이언트가 연결을 최대
    36개만 잡는 동안에는 얻는 것이 없습니다.
- **Footprint.** Postgres는 `VACUUM FULL` 뒤 디스크의 테이블과 인덱스를 셉니다. Redis는 AOF rewrite 뒤의
  `used_memory`를 셉니다. Redis의 `MEMORY USAGE`는 JSON 문서를 크게 추정해서(합이 `used_memory`보다 큼),
  표에는 비율만 적었습니다.
- **`kill -9`.** "다시 쓸 수 있을 때까지 초"는 대부분 kubelet의 재시작 back-off입니다. 이 세션에서 서버마다 네 번씩
  죽였습니다. 엔진 자체의 복구 시간은 로그에 나옵니다.
  - Postgres: redo 0.54초, 시작 후 0.83초에 준비 완료.
  - Redis: AOF 로드 2.0초(strict 실행), 9.3초(relaxed 실행).
- **만료.**
  - 브랜치의 `shared_preload_libraries`에 pg_cron이 없어서, 스크립트가 10초마다 작업을 돌렸습니다.
  - WAL은 서버 전체 값입니다. 첫 번째 1만 행 파티션 실행은 백그라운드 작업 때문에 11.4 MiB로 나왔고, 다시 잰 값(표)은
    0.34 MiB였습니다.
  - autovacuum은 1만 행 테이블을 0.8 MiB로 줄였습니다. 10만 행 테이블은 267.6 MiB로 남았습니다. 공간을 테이블
    안에서 다시 쓸 수는 있지만, 밖으로 돌려주지는 않았습니다.
- **작은 상태.** 클라이언트 프로세스 하나가 worker를 모두 돌렸습니다. 클라이언트 한계로 표시한 실행은 서버보다
  Python을 잰 것입니다. 턴/초보다 지연과 바이트를 비교하세요.
  - C=50에서 `jsonb` 변형은 dead tuple 2,450개(HOT update 97%)와 70 MiB 테이블을 남겼습니다. 메시지당 한 행은
    12 MiB 테이블에 dead tuple이 없었습니다.

#### Recall(미리 넣은 메모리 5만 건, 쿼리 500개, 사용자별 필터)

| 시스템 | recall@5 | recall@10 | 검색 p50 ms | p99 ms |
|---|---|---|---|---|
| postgres | 1.0 | 1.0 | 1.7 | 3.3 |
| redis | 1.0 | 1.0 | 0.9 | 2.0 |

#### 세션, think time 0(포화)

| 내구성 | C | 경로 | 세션 | 오류 | 턴/초 | 턴 오버헤드 p50 ms | p99 | 체크포인트 쓰기 p50 ms | p99 | 메모리 검색 p50 ms | p99 | 서버 CPU 코어 | gateway 코어(throttling) | 클라이언트 CPU ms/턴 | 클라이언트 한계 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| relaxed | 1 | gateway | 30 | 0 | 180.15 | 4.4 | 15.0 | 1.1 | 2.5 | 2.1 | 3.6 | 0.46 | 0.13 (0.0 s) | 4.93 |  |
| relaxed | 1 | gateway-pooler | 30 | 0 | 172.94 | 4.7 | 11.6 | 1.3 | 2.6 | 2.2 | 3.6 | 0.46 | 0.13 (0.0 s) | 4.92 |  |
| relaxed | 1 | redis | 30 | 0 | 239.34 | 4.0 | 8.8 | 0.8 | 1.7 | 1.2 | 2.4 | 0.22 | – | 4.64 | 예 |
| relaxed | 10 | gateway | 100 | 0 | 669.69 | 12.7 | 39.6 | 3.1 | 8.6 | 3.2 | 9.8 | 1.42 | 0.51 (0.0 s) | 7.66 |  |
| relaxed | 10 | gateway-pooler | 100 | 0 | 612.38 | 14.1 | 39.4 | 3.5 | 9.6 | 3.4 | 15.0 | 1.33 | 0.43 (0.0 s) | 7.62 |  |
| relaxed | 10 | redis | 100 | 0 | 966.46 | 9.0 | 19.4 | 1.3 | 3.6 | 2.8 | 7.6 | 0.57 | – | 6.19 |  |
| relaxed | 50 | gateway | 500 | 0 | 722.73 | 62.2 | 113.8 | 15.6 | 34.7 | 5.1 | 29.8 | 1.74 | 0.69 (0.0 s) | 6.38 |  |
| relaxed | 50 | gateway-pooler | 500 | 0 | 647.59 | 69.6 | 147.5 | 17.4 | 43.3 | 7.3 | 41.1 | 1.59 | 0.58 (0.0 s) | 6.54 |  |
| relaxed | 50 | redis | 500 | 0 | 1149.77 | 38.5 | 81.7 | 2.7 | 12.1 | 21.2 | 52.7 | 0.82 | – | 5.01 | 예 |
| strict | 1 | gateway | 30 | 0 | 173.12 | 4.8 | 14.3 | 1.3 | 2.6 | 1.9 | 3.1 | 0.28 | 0.11 (0.0 s) | 4.74 |  |
| strict | 1 | gateway-pooler | 30 | 0 | 164.26 | 5.1 | 14.1 | 1.4 | 3.0 | 2.1 | 3.5 | 0.48 | 0.11 (0.0 s) | 4.84 |  |
| strict | 1 | redis | 30 | 0 | 153.93 | 6.0 | 12.1 | 1.5 | 3.0 | 2.1 | 4.5 | 0.16 | – | 4.92 |  |
| strict | 10 | gateway | 100 | 0 | 598.29 | 15.1 | 26.6 | 3.7 | 8.8 | 3.2 | 7.1 | 1.21 | 0.46 (0.0 s) | 7.44 |  |
| strict | 10 | gateway-pooler | 100 | 0 | 555.86 | 15.9 | 34.6 | 4.1 | 9.6 | 3.2 | 10.1 | 1.19 | 0.43 (0.0 s) | 7.43 |  |
| strict | 10 | redis | 100 | 0 | 382.65 | 21.6 | 55.4 | 3.2 | 9.5 | 9.4 | 29.0 | 0.34 | – | 7.12 |  |
| strict | 50 | gateway | 500 | 0 | 612.34 | 74.6 | 142.9 | 18.9 | 43.4 | 5.0 | 26.0 | 1.66 | 0.6 (0.0 s) | 6.56 |  |
| strict | 50 | gateway-pooler | 500 | 0 | 581.65 | 78.9 | 154.4 | 20.1 | 47.1 | 5.9 | 33.0 | 1.52 | 0.53 (0.0 s) | 6.56 |  |
| strict | 50 | redis | 500 | 0 | 523.69 | 86.9 | 179.4 | 4.0 | 12.7 | 61.1 | 110.5 | 0.49 | – | 5.46 |  |

#### 세션, think time 1–3초

| 내구성 | C | 경로 | 세션 | 오류 | 턴/초 | 턴 오버헤드 p50 ms | p99 | 체크포인트 쓰기 p50 ms | p99 | 메모리 검색 p50 ms | p99 | 서버 CPU 코어 | gateway 코어(throttling) | 클라이언트 CPU ms/턴 | 클라이언트 한계 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| relaxed | 1 | gateway | 2 | 0 | 0.49 | 12.4 | 19.3 | 1.4 | 3.4 | 3.1 | 6.0 | 0.42 | 0.0 (0.0 s) | 15.94 |  |
| relaxed | 1 | redis | 2 | 0 | 0.49 | 11.9 | 20.9 | 1.2 | 5.6 | 2.1 | 5.9 | 0.01 | – | 16.78 |  |
| relaxed | 10 | gateway | 20 | 0 | 4.56 | 12.9 | 36.2 | 1.8 | 6.5 | 3.7 | 8.2 | 0.16 | 0.01 (0.0 s) | 15.9 |  |
| relaxed | 10 | redis | 20 | 0 | 4.56 | 12.6 | 22.2 | 1.5 | 5.1 | 2.5 | 6.3 | 0.02 | – | 15.97 |  |
| relaxed | 50 | gateway | 100 | 0 | 21.8 | 8.9 | 42.9 | 1.3 | 19.6 | 3.1 | 23.8 | 0.13 | 0.03 (0.0 s) | 8.3 |  |
| relaxed | 50 | redis | 100 | 0 | 22.14 | 8.9 | 39.2 | 1.2 | 7.2 | 2.0 | 24.6 | 0.04 | – | 8.71 |  |
| strict | 1 | gateway | 2 | 0 | 0.49 | 17.3 | 60.7 | 2.5 | 16.7 | 3.8 | 15.2 | 0.09 | 0.0 (0.0 s) | 18.04 |  |
| strict | 1 | redis | 2 | 0 | 0.49 | 21.3 | 111.8 | 3.2 | 10.3 | 4.2 | 7.4 | 0.01 | – | 20.11 |  |
| strict | 10 | gateway | 20 | 0 | 4.55 | 13.6 | 33.0 | 2.2 | 6.3 | 3.4 | 9.3 | 0.04 | 0.01 (0.0 s) | 16.3 |  |
| strict | 10 | redis | 20 | 0 | 4.55 | 15.3 | 29.3 | 2.5 | 6.9 | 3.1 | 12.7 | 0.02 | – | 15.53 |  |
| strict | 50 | gateway | 100 | 0 | 21.87 | 9.1 | 52.9 | 1.6 | 54.2 | 2.8 | 30.3 | 0.15 | 0.03 (0.0 s) | 8.17 |  |
| strict | 50 | redis | 100 | 0 | 22.0 | 11.0 | 52.8 | 2.0 | 9.3 | 2.6 | 32.4 | 0.04 | – | 8.26 |  |

#### 끝난 세션 하나가 차지하는 크기(세션 100개 x 16턴, 압축한 상태에서 시작)

| 시스템 | 위치 | 세션당 KiB | GiB당 세션 | 큰 부분 |
|---|---|---|---|---|
| postgres | 디스크(테이블 + 인덱스) | 149.4 | 7,016 | checkpoint_blobs 60.0 KiB, checkpoints 48.2 KiB, checkpoint_writes 35.8 KiB, store_vectors 5.5 KiB |
| redis | RAM(used_memory) | 435.5 | 2,407 | checkpoint 86%, checkpoint_write 7%, write_keys_zset 4%, store_vectors 2% |

#### 부하 중 kill -9(writer 10개, think time 0)

| 내구성 | 시스템 | ack된 체크포인트 | 잃은 것 | 쓰기/초 | 다시 쓸 수 있을 때까지 초 | 메모 |
|---|---|---|---|---|---|---|
| relaxed | postgres | 10529 | 38 | 636.3 | 48.12 |  |
| relaxed | redis | 8833 | 0 | 564.1 | 61.63 |  |
| strict | postgres | 9704 | 0 | 583.7 | 24.58 |  |
| strict | redis | 10107 | 0 | 648.5 | 30.04 |  |

#### 만료(2 KiB 랜덤 payload, TTL 60초)

| 세션 | 변형 | 만료 방식 | 쓰기 초 | 살아 있을 때 MiB | 만료 후 사라질 때까지 초 | 만료에 쓴 WAL/AOF MiB | 사라진 뒤 남은 MiB | 사라진 시점 dead tuple | autovacuum까지 초 | autovacuum 후 MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| 10,000 | pg_delete | 스크립트 10초마다 | 0.85 | 27.5 | 1.1 | 2.02 | 27.5 | 10000 | 40.1 | 0.8 |
| 10,000 | pg_partition | 스크립트 10초마다 | 0.91 | 27.5 | 11.3 | 0.34 | 0.2 | – | – | – |
| 10,000 | redis_ttl | TTL | 2.28 | 25.2 | 0.4 | 0.27 | 0.5 | – | – | – |
| 100,000 | pg_delete | 스크립트 10초마다 | 7.95 | 274.1 | 8.3 | 19.98 | 274.2 | 100000 | 15.0 | 267.6 |
| 100,000 | pg_partition | 스크립트 10초마다 | 7.62 | 271.6 | 18.4 | 2.21 | 0.2 | – | – | – |
| 100,000 | redis_ttl | TTL | 9.3 | 250.9 | 0.4 | 2.84 | 0.5 | – | – | – |

#### 매 턴 고쳐 쓰는 작은 상태(16턴, 600자 메시지)

| C | 변형 | 턴 | 턴/초 | 읽기+덧붙이기 p50 ms | p99 | 턴당 WAL/AOF 바이트 | 테이블 MiB | dead tuple | HOT 비율 | autovacuum 횟수 | 클라이언트 한계 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | pg_jsonb | 3200 | 5716.1 | 1.4 | 7.9 | 6326 | 17.1 | 475 | 0.972 | 0 | 예 |
| 10 | pg_rows | 3200 | 6284.4 | 1.2 | 14.0 | 874 | 2.5 | 0 | – | 0 | 예 |
| 10 | redis_list | 3200 | 4996.7 | 1.8 | 5.5 | 681 | – | – | – | – |  |
| 50 | pg_jsonb | 16000 | 5682.7 | 6.1 | 37.8 | 6422 | 70.2 | 2450 | 0.968 | 1 |  |
| 50 | pg_rows | 16000 | 3957.6 | 7.6 | 81.1 | 874 | 12.4 | 0 | – | 0 |  |
| 50 | redis_list | 16000 | 8810.9 | 5.0 | 29.7 | 681 | – | – | – | – | 예 |

#### 미리 넣은 장기 메모리(사용자 1,000명 x 50건, 384차원)

| 시스템 | 메모리 | 위치 | MiB | 메모리당 KiB | 내역(MiB) |
|---|---|---|---|---|---|
| postgres | 50000 | 디스크, VACUUM FULL 후 | 184.6 | 3.78 | store_vectors heap 78.1 + indexes 99.6; store 6.8 |
| redis | 50000 | RAM, AOF에서 재시작한 뒤 | 324.5 | 6.65 | used_memory_dataset 319.1 |

#### 덧붙이기 한 번이 쓰는 바이트, 턴별(세션 하나)

| 변형 | turn 1 | turn 2 | turn 4 | turn 8 | turn 12 | turn 16 |
|---|---|---|---|---|---|---|
| pg_jsonb | 1032 | 1480 | 3200 | 5976 | 8768 | 11664 |
| pg_rows | 928 | 832 | 832 | 832 | 976 | 856 |
| redis_list | 673 | 673 | 673 | 673 | 673 | 673 |

### 무엇을 돌리나

모두 [`../platform`](../platform/)의 kind 클러스터에서 돌립니다. Postgres 쪽은 빈 브랜치 `agentmem`
(`xata.micro`, scale-to-zero 끔)이 맡습니다. Redis 쪽은 같은 클러스터의 Redis pod가 맡습니다. 클라이언트는 벤치
pod에서 돌리므로, 두 시스템 모두 같은 클러스터 네트워크로 접속합니다.

| | Postgres(Xata 브랜치) | Redis |
|---|---|---|
| 엔진 | PostgreSQL 18.6, pgvector 0.8.7 | Redis Open Source 8.10.3(Query Engine, JSON 포함) |
| 리소스 | Postgres 924 Mi, 2 CPU, PgBouncer 100 Mi, 0.5 CPU(Xata의 `xata.micro`) | 1 GiB, 2 CPU, `maxmemory 900mb`, `noeviction` |
| LangGraph checkpointer | `AsyncPostgresSaver`(langgraph-checkpoint-postgres 3.2.0) | `AsyncRedisSaver`(langgraph-checkpoint-redis 0.6.0) |
| LangGraph store | `AsyncPostgresStore`, pgvector HNSW `m=16, ef_construction=64`, `ef_search` 40 | `AsyncRedisStore`, HNSW `m=16, ef_construction=64, ef_runtime=40` |
| 경로 | **gateway**: `xata branch url`이 주는 연결 문자열. 클라이언트 → SQL gateway(TLS) → Postgres. **gateway-pooler**: 호스트 첫 부분을 `<branch>-pooler`로 바꾼 것. 클라이언트 → gateway → PgBouncer(transaction 모드) → Postgres | 클라이언트 → Redis |

**에이전트**([`bench/agentmem.py`](bench/agentmem.py))는 노드 하나짜리 LangGraph 그래프입니다. "모델"은 think time만큼
쉬고 고정된 600자로 답합니다. LLM은 부르지 않습니다. 세션 하나는 이렇게 진행됩니다.

1. thread 상태를 불러옵니다.
2. 16턴을 돕니다. 턴마다 세션 사용자의 메모리 5개를 꺼내고(`store.asearch`, 사용자 namespace로 필터), think time만큼
   쉬고, LangGraph가 체크포인트를 씁니다.
3. 4턴마다 임베딩과 함께 새 장기 메모리를 씁니다.

실행 전에 양쪽 store에 사용자 1,000명 × 메모리 50건을 넣습니다. 임베딩은 seed를 고정한 합성 값입니다
([`bench/vectors.py`](bench/vectors.py)). 384차원이고, 주제 중심 64개에 노이즈를 더했습니다. 모델이나 API를 부르지
않으므로 recall의 정답을 정확히 계산할 수 있습니다.

**세션 매트릭스**는 내구성 짝 2개 × think time 2가지 × 동시성 1 / 10 / 50입니다. 구성마다 gateway 경로와 Redis에서
돌립니다. think time 0에서는 Postgres를 gateway-pooler 경로로도 돌립니다. 모두 30번입니다. 클라이언트 프로세스마다
Postgres 연결을 최대 6개, 모두 36개까지만 잡습니다. gateway 경로가 Postgres로 바로 가고, `xata.micro`의
`max_connections`가 50이기 때문입니다. Redis 클라이언트는 제한하지 않습니다.

- **내구성 짝.** *strict*는 Redis `appendfsync always`와 Postgres `synchronous_commit=on`입니다. *relaxed*는
  `everysec`와 `off`입니다. Redis는 AOF만 씁니다(RDB 스냅샷 끔).
- **think time 0**은 포화 상태를 잽니다. 연산별 p50/p99와 초당 턴 수입니다. C=1이면 세션 30개, 그 밖에는 worker마다
  10개를 돌립니다.
- **think time 1–3초**(균등 분포)는 실제 에이전트에 가깝습니다. worker마다 세션 2개를 돌립니다.

**만료**([`bench/expiry.py`](bench/expiry.py)): 2 KiB 랜덤 payload를 가진 세션 1만 개와 10만 개, TTL 60초, strict
내구성입니다.
- `redis_ttl`: `SET … EX 60`.
- `pg_delete`: 테이블 하나와 10초마다 도는 `DELETE … WHERE expires_at < now()`. 브랜치의
  `shared_preload_libraries`에 pg_cron이 없어서, 스크립트가 같은 문장을 같은 주기로 돌립니다.
- `pg_partition`: 폭 10초의 시간 파티션. 파티션의 상한이 지나면 그 파티션을 drop합니다. pg_partman의 retention이
  하는 일과 같습니다(pg_partman 5.4.1은 있지만 쓰지 않았습니다).

**매 턴 고쳐 쓰는 작은 상태**([`bench/hotstate.py`](bench/hotstate.py)): 세션의 메시지 목록을 턴마다 한 번 읽고
덧붙이며 16턴을 돕니다. 메시지는 랜덤 가짜 단어로 만든 600자입니다. strict 내구성, 동시성 10과 50, worker마다 세션
20개입니다. Postgres는 gateway-pooler 경로로 돌리고, worker마다 연결 하나를 세션 동안 잡습니다.
- `redis_list`: `LRANGE` + `RPUSH`.
- `pg_jsonb`: 세션마다 `jsonb` 행 하나를 턴마다 통째로 다시 씁니다(흔한 패턴).
- `pg_rows`: 메시지마다 한 행(Postgres다운 설계).

세션 하나짜리 probe가 턴마다 덧붙이기 한 번이 쓰는 WAL 또는 AOF 바이트를 기록합니다.

**크래시**([`run.sh kill`](run.sh)): think time 0인 writer 10개를 돌립니다. 15초 뒤 서버 프로세스에 `SIGKILL`을
보냅니다. Redis는 kind 노드에서 `redis-server`를, Postgres는 브랜치 pod 안의 postmaster를 죽입니다. 첫 오류가 나면
모든 writer가 멈춥니다. 그다음 서버가 다시 쓰기를 받을 때까지 기다리고, 오류 전에 ack된 체크포인트가 모두 남아
있는지 확인합니다.

**크기.** `setup`은 끝난 세션 하나의 크기도 잽니다. 압축한 상태(Postgres는 `VACUUM FULL`, Redis는 AOF rewrite)에서
세션 100개를 돌립니다. `memsize`는 미리 넣은 메모리 5만 건의 크기를 잽니다. Postgres는 `VACUUM FULL` 뒤, Redis는
AOF에서 재시작한 뒤입니다.

### 공정성 규칙

- 그래프, 데이터, 임베딩이 같습니다. checkpointer와 store만 바뀝니다.
- 양쪽 checkpointer 모두 전체 이력을 남깁니다(`ShallowRedisSaver`가 아니라 `AsyncRedisSaver`).
- 양쪽 모두 HNSW이고 `m`과 `ef_construction`이 같습니다. `AsyncRedisStore`의 기본값은 FLAT(brute-force) 인덱스이고,
  redisvl의 HNSW 기본값도 다릅니다(`ef_construction` 200, `ef_runtime` 10). 이 실습은 Redis에 pgvector의 값을
  넣습니다(`bench/agentmem.py`의 `REDIS_INDEX`). 지연을 재기 전에 recall을 먼저 잽니다.
- 내구성은 짝으로 맞춥니다. Postgres는 role에 설정합니다(`ALTER ROLE … SET synchronous_commit`). 브랜치의 PgBouncer가
  transaction 모드라서 세션 `SET`이 유지되지 않기 때문입니다. 그다음 pool의 연결을 모두 확인합니다
  (`bench/ctl.py pg-sync`).
- 실행 중에는 Redis AOF 자동 rewrite를 끕니다. 실행 사이에 AOF를 직접 압축합니다.
- Postgres 실행마다 role의 idle backend를 먼저 끊습니다(`bench/ctl.py pg-idle-reset`). 앞 실행에서 남은 PgBouncer의
  idle 서버 연결이 Postgres 슬롯을 차지하지 않게 하기 위해서입니다.
- 처리량은 턴이 실제로 돈 구간으로 계산합니다. 프로세스 시작과 연결 시간은 빠집니다.
- 세션은 끝나면 자기가 만든 것을 지웁니다(`adelete_thread`, store 삭제). 그래서 실행마다 같은 상태에서 시작합니다.
- 클라이언트는 프로세스 6개로 돕니다. 클라이언트 CPU가 그 85%에 이르면 클라이언트 한계로 표시합니다.

### 실행

```bash
oss/platform/up.sh                 # 한 번
oss/agent-memory-redis/run.sh      # up, setup, sessions, kill, expiry, hotstate, memsize: 약 45분
oss/agent-memory-redis/report.py   # results/*.jsonl에서 표 만들기(--lang ko는 한국어 제목)
oss/agent-memory-redis/run.sh down # Redis, 벤치 pod, agentmem 브랜치 삭제
```

단계별로도 돌릴 수 있습니다: `run.sh up | setup | sessions | kill | expiry | hotstate | memsize`. 세션 구성 하나만
돌리려면 `run.sh one <gateway|gateway_pooler> <postgres|redis> <label> <0|real> <C> <note>`입니다. 실행 시간이 길어서
`oss/run-all.sh`에는 넣지 않았습니다.

### 로컬 조정

- **이 실습을 돌리는 동안 SQL gateway를 chart의 리소스로 둡니다.** upstream의 로컬 overlay는 노트북에 맞추려고
  gateway를 줄입니다. `GOMAXPROCS=1`, CPU limit 200m입니다(`kustomize/overlays/local/kustomization.yaml`의 `gateway`
  patch). gateway chart의 값은 `GOMAXPROCS=8`, CPU limit 8, request 4입니다(`charts/gateway/values.yaml`,
  `templates/deployment.yaml`). 200m에서는 Postgres가 아니라 gateway가 모든 Postgres 숫자를 정했습니다(위의 함께 찾은
  것). `run.sh up`이 chart 값을 적용하고, `run.sh down`이 overlay 값으로 되돌립니다.
- 벤치 pod는 gateway Service의 cluster IP로 접속하면서 TLS SNI에는 브랜치 호스트명을 씁니다(연결 문자열의 `hostaddr=`).
  클러스터 안에서는 `*.127.0.0.1.nip.io`가 pod 자신을 가리키기 때문입니다. 플랫폼은 루트 README의 목록 밖으로 바꾸지
  않았습니다.

### 한계

- 노트북 한 대입니다. Docker Desktop 16 GiB 위의 kind이고, pod가 모두 노드 하나에 있습니다. 플랫폼이 메모리 요청량을 약
  9 GiB 씁니다. 숫자는 같은 조건에서 두 시스템을 비교하는 용도이고, 용량 산정용이 아닙니다.
- Postgres의 "세션당 바이트"는 디스크이고, Redis는 RAM입니다. 그 차이가 요점이지만, 같은 자원은 아닙니다.
- OSS만 씁니다. Xata Cloud, Redis Cloud, ClickHouse는 쓰지 않습니다.
- 이 실습은 브랜치 시간을 재지 않습니다. 이 기계의 브랜치 시간은 CoW 숫자가 아닙니다(루트 README, 발견 사항 4).
- **여기서 재지 않은 Redis의 강점:**
  - 작업 큐(Streams consumer group, `XAUTOCLAIM`)
  - 만료가 붙은 락과 rate limit(`SET NX PX`, `INCR`, Lua)
  - 진행 상황을 흘려보내는 pub/sub
  - eviction이 있는 semantic cache
  - 클라이언트 50개를 넘는 연결
