"""Shared helpers: connections, timing, percentiles, server-side counters, result lines.

Connection strings come from the environment (PG_URL, REDIS_URL) and are never printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager

import numpy as np

PG_URL = os.environ.get("PG_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis.hols-bench.svc:6379/0")


def now() -> float:
    return time.perf_counter()


def stats(samples_ms: list[float]) -> dict:
    if not samples_ms:
        return {"n": 0}
    a = np.asarray(samples_ms)
    return {
        "n": int(a.size),
        "p50": round(float(np.percentile(a, 50)), 3),
        "p99": round(float(np.percentile(a, 99)), 3),
        "mean": round(float(a.mean()), 3),
        "max": round(float(a.max()), 3),
    }


def emit(record: dict) -> None:
    """One JSON line on stdout: run.sh appends it to the lab's results file."""
    record.setdefault("utc", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


# --- Postgres --------------------------------------------------------------------------

async def pg_conn(autocommit: bool = True):
    import psycopg
    from psycopg.rows import dict_row

    conn = await psycopg.AsyncConnection.connect(
        PG_URL, autocommit=autocommit, prepare_threshold=0, row_factory=dict_row
    )
    return conn


@asynccontextmanager
async def pg_pool(size: int):
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        PG_URL,
        min_size=size,
        max_size=size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=120)
    try:
        yield pool
    finally:
        await pool.close()


async def pg_one(sql: str, *args):
    conn = await pg_conn()
    try:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchone()
    finally:
        await conn.close()


async def pg_wal_lsn(conn) -> str:
    cur = await conn.execute("select pg_current_wal_lsn()::text as lsn")
    return (await cur.fetchone())["lsn"]


async def pg_wal_bytes(conn, since: str) -> int:
    cur = await conn.execute("select pg_wal_lsn_diff(pg_current_wal_lsn(), %s::pg_lsn)::bigint as b", (since,))
    return int((await cur.fetchone())["b"])


async def pg_rel_bytes(conn, tables: list[str]) -> dict:
    out = {}
    for t in tables:
        cur = await conn.execute("select coalesce(pg_total_relation_size(to_regclass(%s)), 0)::bigint as b", (t,))
        out[t] = int((await cur.fetchone())["b"])
    return out


async def pg_table_stats(conn, table: str) -> dict:
    cur = await conn.execute(
        """select n_live_tup, n_dead_tup, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del,
                  autovacuum_count, vacuum_count
             from pg_stat_user_tables where relname = %s""",
        (table,),
    )
    row = await cur.fetchone()
    return {k: int(v) for k, v in (row or {}).items()}


async def pg_settings(conn) -> dict:
    cur = await conn.execute(
        """select name, setting from pg_settings
            where name in ('synchronous_commit','fsync','wal_level','shared_buffers',
                           'autovacuum_naptime','hnsw.ef_search','server_version')"""
    )
    return {r["name"]: r["setting"] for r in await cur.fetchall()}


# --- Redis -----------------------------------------------------------------------------

def redis_client(decode: bool = False, db: int | None = None):
    import redis.asyncio as aredis

    url = REDIS_URL
    if db is not None:
        url = url.rsplit("/", 1)[0] + f"/{db}"
    return aredis.Redis.from_url(url, decode_responses=decode, max_connections=512)


async def redis_info(r) -> dict:
    mem = await r.info("memory")
    per = await r.info("persistence")
    srv = await r.info("server")
    return {
        "used_memory": int(mem["used_memory"]),
        "used_memory_rss": int(mem["used_memory_rss"]),
        "aof_enabled": int(per.get("aof_enabled", 0)),
        "aof_current_size": int(per.get("aof_current_size", 0) or 0),
        "redis_version": srv["redis_version"],
    }


async def redis_durability(r, mode: str) -> dict:
    """mode: 'always' or 'everysec'. AOF on, RDB snapshots off, so AOF is the only persistence."""
    await r.config_set("appendonly", "yes")
    await r.config_set("appendfsync", mode)
    await r.config_set("save", "")
    # no automatic AOF rewrite during a measurement: it would reset aof_current_size mid-run.
    # agentmem.py compacts the AOF itself between runs (BGREWRITEAOF after cleanup).
    await r.config_set("auto-aof-rewrite-percentage", "0")
    # wait for the initial AOF rewrite that turning AOF on starts
    for _ in range(600):
        per = await r.info("persistence")
        if not int(per.get("aof_rewrite_in_progress", 0)) and not int(per.get("aof_rewrite_scheduled", 0)):
            break
        await asyncio.sleep(0.2)
    return await redis_durability_get(r)


async def redis_durability_get(r) -> dict:
    cfg = {}
    for k in ("appendonly", "appendfsync", "save", "maxmemory", "maxmemory-policy", "auto-aof-rewrite-percentage"):
        cfg.update(await r.config_get(k))
    return {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in cfg.items()}


async def redis_compact_aof(r) -> None:
    await r.bgrewriteaof()
    for _ in range(1200):
        per = await r.info("persistence")
        if not int(per.get("aof_rewrite_in_progress", 0)) and not int(per.get("aof_rewrite_scheduled", 0)):
            return
        await asyncio.sleep(0.25)
