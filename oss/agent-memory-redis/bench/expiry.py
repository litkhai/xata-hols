"""Expiry: N short-lived sessions, how long until they are gone and what it costs.

  expiry.py --variant redis_ttl    SET ... EX <ttl> on Redis (logical db 1)
  expiry.py --variant pg_delete    a table + a DELETE ... WHERE expires_at < now() job every 10 s
                                   (pg_cron when the role may use it, else this script on the same schedule)
  expiry.py --variant pg_partition time partitions 1/6 of the TTL wide; the job drops each partition
                                   once its upper bound has passed (what pg_partman retention does)

The harness writes this directly, without LangGraph, so the mechanism is what gets measured.
Payloads are random hex, so neither side gets a free ride from compression.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

from psycopg import sql

import common as c

JOB_EVERY_S = 10


def payload(i: int, size: int) -> str:
    return os.urandom(size // 2).hex()


async def redis_ttl(n: int, ttl: int, size: int) -> dict:
    r = c.redis_client(db=1)
    try:
        await r.flushdb()
        await quiesce(r)
        base = await c.redis_info(r)
        t0 = time.time()
        for start in range(0, n, 1000):
            p = r.pipeline(transaction=False)
            for i in range(start, min(n, start + 1000)):
                p.set(f"sess:{i}", payload(i, size), ex=ttl)
            await p.execute()
        t_written = time.time()
        loaded = await c.redis_info(r)
        sample = [n for n in await asyncio.gather(*(r.memory_usage(f"sess:{i}", samples=0) for i in range(0, n, max(1, n // 200))))]
        expire_at = t_written + ttl  # the last key expires here
        samples = []
        gone_at = None
        aof_at_expiry = None
        while time.time() < expire_at + 600:
            left = await r.dbsize()
            info = await c.redis_info(r)
            now = time.time()
            if aof_at_expiry is None and now >= t0 + ttl:
                aof_at_expiry = info["aof_current_size"]
            samples.append((round(now - expire_at, 1), left, info["used_memory"]))
            if left == 0 and now >= expire_at:
                gone_at = now
                break
            await asyncio.sleep(1)
        await quiesce(r)
        end = await c.redis_info(r)
        return {
            "write_s": round(t_written - t0, 2),
            "bytes_while_live": loaded["used_memory"] - base["used_memory"],
            "bytes_while_live_memory_usage": round(sum(sample) / len(sample) * n),
            "seconds_after_expiry_until_gone": round(gone_at - expire_at, 1) if gone_at else None,
            "bytes_left_after": end["used_memory"] - base["used_memory"],
            "aof_bytes_for_expiry": end["aof_current_size"] - (aof_at_expiry or end["aof_current_size"]),
            "samples": samples[-20:],
        }
    finally:
        await r.aclose()


async def quiesce(r) -> None:
    """Wait until lazy frees and AOF rewrites are done, so used_memory is not moving under us."""
    for _ in range(240):
        mem, per = await r.info("memory"), await r.info("persistence")
        if not int(mem.get("lazyfree_pending_objects", 0)) and not int(per.get("aof_rewrite_in_progress", 0)):
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(1)


async def pg_cron_usable(conn) -> bool:
    try:
        cur = await conn.execute("select count(*) as n from pg_extension where extname = 'pg_cron'")
        if not (await cur.fetchone())["n"]:
            await conn.execute("create extension if not exists pg_cron")
        await conn.execute("select cron.unschedule(jobid) from cron.job where jobname = 'hols_expiry'")
        return True
    except Exception as e:
        c.log(f"pg_cron not usable here: {type(e).__name__}: {str(e)[:160]}")
        return False


async def insert_rows(conn, table: str, n: int, ttl: int, size: int) -> float:
    t0 = time.time()
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    for start in range(0, n, 1000):
        ids = list(range(start, min(n, start + 1000)))
        async with conn.cursor() as cur:
            await cur.execute(
                f"insert into {table} (id, expires_at, payload) "
                "select i, %s, jsonb_build_object('s', p) from unnest(%s::bigint[], %s::text[]) as t(i, p)",
                (expires, ids, [payload(i, size) for i in ids]),
            )
    return expires.timestamp()


async def pg_variant(variant: str, n: int, ttl: int, size: int) -> dict:
    conn = await c.pg_conn()
    table = "hols_sess" if variant == "pg_delete" else "hols_sess_p"
    try:
        await conn.execute("drop table if exists hols_sess, hols_sess_p cascade")
        width = max(1, ttl // 6)
        if variant == "pg_delete":
            await conn.execute("create table hols_sess (id bigint primary key, expires_at timestamptz not null, payload jsonb not null)")
            await conn.execute("create index on hols_sess (expires_at)")
        else:
            await conn.execute("create table hols_sess_p (id bigint, expires_at timestamptz not null, payload jsonb not null) partition by range (expires_at)")
            start = datetime.now(timezone.utc).replace(microsecond=0)
            for k in range(0, (ttl + 300) // width + 2):
                lo, hi = start + timedelta(seconds=k * width), start + timedelta(seconds=(k + 1) * width)
                # DDL takes no bind parameters: the bounds go in as literals
                await conn.execute(sql.SQL("create table {} partition of hols_sess_p for values from ({}) to ({})").format(
                    sql.Identifier(f"hols_sess_p_{k}"), sql.Literal(lo), sql.Literal(hi)))
        use_cron = variant == "pg_delete" and await pg_cron_usable(conn)
        lsn0 = await c.pg_wal_lsn(conn)
        t0 = time.time()
        expire_at = await insert_rows(conn, table, n, ttl, size)
        t_written = time.time()
        live = (await c.pg_rel_bytes(conn, [table]))[table] if variant == "pg_delete" else await part_bytes(conn)
        wal_insert = await c.pg_wal_bytes(conn, lsn0)
        if use_cron:
            await conn.execute(
                "select cron.schedule('hols_expiry', %s, 'delete from hols_sess where expires_at < now()')",
                (f"{JOB_EVERY_S} seconds",))
        lsn1 = await c.pg_wal_lsn(conn)
        gone_at, last_job = None, 0.0
        samples = []
        while time.time() < expire_at + 600:
            now = time.time()
            if not use_cron and now - last_job >= JOB_EVERY_S:
                last_job = now
                if variant == "pg_delete":
                    await conn.execute("delete from hols_sess where expires_at < now()")
                else:
                    await drop_expired_partitions(conn)
            cur = await conn.execute(f"select count(*) as n from {table}")
            left = int((await cur.fetchone())["n"])
            samples.append((round(now - expire_at, 1), left))
            if left == 0 and now >= expire_at:
                gone_at = now
                break
            await asyncio.sleep(1)
        wal_expiry = await c.pg_wal_bytes(conn, lsn1)
        after_gone = (await c.pg_rel_bytes(conn, [table]))[table] if variant == "pg_delete" else await part_bytes(conn)
        out = {
            "job": "pg_cron" if use_cron else f"script every {JOB_EVERY_S}s",
            "write_s": round(t_written - t0, 2),
            "bytes_while_live": live,
            "wal_bytes_insert": wal_insert,
            "seconds_after_expiry_until_gone": round(gone_at - expire_at, 1) if gone_at else None,
            "wal_bytes_for_expiry": wal_expiry,
            "bytes_left_when_gone": after_gone,
            "samples": samples[-20:],
        }
        if variant == "pg_delete":
            # dead rows stay until (auto)vacuum; wait for one autovacuum pass, up to 5 minutes
            await asyncio.sleep(11)  # backends flush their table statistics when idle ~10 s
            stats0 = await c.pg_table_stats(conn, table)
            st = stats0
            out["dead_tuples_when_gone"] = st.get("n_dead_tup")
            t_v = time.time()
            while time.time() - t_v < 300:
                st = await c.pg_table_stats(conn, table)
                if st.get("autovacuum_count", 0) > stats0.get("autovacuum_count", 0):
                    break
                await asyncio.sleep(5)
            out["autovacuum_ran"] = st.get("autovacuum_count", 0) > stats0.get("autovacuum_count", 0)
            out["seconds_until_autovacuum"] = round(time.time() - t_v, 1) if out["autovacuum_ran"] else None
            out["bytes_after_autovacuum"] = (await c.pg_rel_bytes(conn, [table]))[table]
            out["dead_tuples_after_autovacuum"] = st.get("n_dead_tup")
            if use_cron:
                await conn.execute("select cron.unschedule('hols_expiry')")
        out["settings"] = await c.pg_settings(conn)
        await conn.execute("drop table if exists hols_sess, hols_sess_p cascade")
        return out
    finally:
        await conn.close()


async def part_bytes(conn) -> int:
    cur = await conn.execute(
        "select coalesce(sum(pg_total_relation_size(inhrelid)), 0)::bigint as b from pg_inherits "
        "where inhparent = 'hols_sess_p'::regclass")
    return int((await cur.fetchone())["b"])


async def drop_expired_partitions(conn) -> None:
    cur = await conn.execute(
        """select c.relname, pg_get_expr(c.relpartbound, c.oid) as bound
             from pg_inherits i join pg_class c on c.oid = i.inhrelid
            where i.inhparent = 'hols_sess_p'::regclass""")
    for row in await cur.fetchall():
        hi = row["bound"].split("TO ('")[1].split("')")[0]
        cur2 = await conn.execute("select %s::timestamptz <= now() as past", (hi,))
        if (await cur2.fetchone())["past"]:
            await conn.execute(f"drop table {row['relname']}")


async def main_async(a) -> None:
    if a.variant == "redis_ttl":
        res = await redis_ttl(a.n, a.ttl, a.size)
    else:
        res = await pg_variant(a.variant, a.n, a.ttl, a.size)
    c.emit({"kind": "expiry", "variant": a.variant, "sessions": a.n, "ttl_s": a.ttl, "payload_bytes": a.size, **res})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["redis_ttl", "pg_delete", "pg_partition"], required=True)
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--ttl", type=int, default=60)
    ap.add_argument("--size", type=int, default=2048)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
