"""Small state rewritten every turn: a session's message list, read and appended once per turn.

  hotstate.py --variant redis_list   LRANGE + RPUSH on wm:<sid>
  hotstate.py --variant pg_jsonb     SELECT + UPDATE ... SET messages = messages || $msg   (one jsonb row per session)
  hotstate.py --variant pg_rows      SELECT ... ORDER BY seq + INSERT                      (one row per message)

  --probe   one session, sequential: the WAL / AOF bytes of each turn's append, turn by turn

Messages are 600 characters of random pseudo-words: about as compressible as chat text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid
from collections import defaultdict

import common as c

TURNS = 16
MSG_CHARS = 600
_VOCAB = ["".join(random.Random(i).choices("abcdefghijklmnopqrstuvwxyz", k=3 + i % 7)) for i in range(2000)]


def message(rng: random.Random) -> str:
    words, n = [], 0
    while n < MSG_CHARS:
        w = rng.choice(_VOCAB)
        words.append(w)
        n += len(w) + 1
    return json.dumps({"role": "assistant", "content": " ".join(words)[:MSG_CHARS]})


async def pg_setup(conn, variant: str) -> str:
    await conn.execute("drop table if exists hols_wm, hols_wm_msgs")
    if variant == "pg_jsonb":
        await conn.execute("create table hols_wm (sid text primary key, messages jsonb not null default '[]')")
        return "hols_wm"
    await conn.execute("create table hols_wm_msgs (sid text, seq int, msg jsonb not null, primary key (sid, seq))")
    return "hols_wm_msgs"


async def pg_turn(conn, variant: str, sid: str, seq: int, msg: str) -> None:
    if variant == "pg_jsonb":
        await conn.execute("select messages from hols_wm where sid = %s", (sid,))
        await conn.execute(
            "insert into hols_wm (sid, messages) values (%s, jsonb_build_array(%s::jsonb)) "
            "on conflict (sid) do update set messages = hols_wm.messages || excluded.messages",
            (sid, msg))
    else:
        await conn.execute("select msg from hols_wm_msgs where sid = %s order by seq", (sid,))
        await conn.execute("insert into hols_wm_msgs (sid, seq, msg) values (%s, %s, %s::jsonb)", (sid, seq, msg))


async def run(variant: str, concurrency: int, spw: int) -> dict:
    rec = defaultdict(list)
    rng = random.Random(11)
    cpu0 = time.process_time()
    if variant == "redis_list":
        r = c.redis_client(db=2)
        await r.flushdb()
        before = await c.redis_info(r)

        async def worker(w):
            wr = random.Random(w)
            for _ in range(spw):
                key = f"wm:{uuid.uuid4().hex[:12]}"
                for t in range(TURNS):
                    msg = message(wr)
                    t0 = c.now()
                    await r.lrange(key, 0, -1)
                    await r.rpush(key, msg)
                    rec["turn"].append((c.now() - t0) * 1000)

        t0 = c.now()
        await asyncio.gather(*(worker(w) for w in range(concurrency)))
        wall = c.now() - t0
        after = await c.redis_info(r)
        turns = len(rec["turn"])
        out = {"aof_bytes_per_turn": round((after["aof_current_size"] - before["aof_current_size"]) / turns),
               "bytes_per_session_live": round((after["used_memory"] - before["used_memory"]) / (concurrency * spw)),
               "redis_version": after["redis_version"],
               "config": await c.redis_durability_get(r)}
        await r.flushdb()
        await c.redis_compact_aof(r)
        await r.aclose()
    else:
        conn = await c.pg_conn()
        table = await pg_setup(conn, variant)
        st0 = await c.pg_table_stats(conn, table)
        lsn0 = await c.pg_wal_lsn(conn)
        async with c.pg_pool(concurrency) as pool:
            async def worker(w):
                wr = random.Random(w)
                async with pool.connection() as wc:
                    for _ in range(spw):
                        sid = uuid.uuid4().hex[:12]
                        for t in range(TURNS):
                            msg = message(wr)
                            t0 = c.now()
                            await pg_turn(wc, variant, sid, t, msg)
                            rec["turn"].append((c.now() - t0) * 1000)

            t0 = c.now()
            await asyncio.gather(*(worker(w) for w in range(concurrency)))
            wall = c.now() - t0
        turns = len(rec["turn"])
        wal = await c.pg_wal_bytes(conn, lsn0)
        await asyncio.sleep(12)  # pooled server backends flush table statistics only after ~10 s idle
        st1 = await c.pg_table_stats(conn, table)
        upd = st1.get("n_tup_upd", 0) - st0.get("n_tup_upd", 0)
        out = {"wal_bytes_per_turn": round(wal / turns),
               "table_bytes": (await c.pg_rel_bytes(conn, [table]))[table],
               "dead_tuples": st1.get("n_dead_tup"),
               "updates": upd,
               "hot_update_share": round((st1.get("n_tup_hot_upd", 0) - st0.get("n_tup_hot_upd", 0)) / upd, 3) if upd else None,
               "autovacuum_runs": st1.get("autovacuum_count", 0) - st0.get("autovacuum_count", 0),
               "settings": await c.pg_settings(conn)}
        await conn.execute("drop table if exists hols_wm, hols_wm_msgs")
        await conn.close()
    client_cores = (time.process_time() - cpu0) / wall
    return {"concurrency": concurrency, "sessions": concurrency * spw, "turns": turns,
            "client_cpu_cores": round(client_cores, 2), "client_bound": client_cores >= 0.85,
            "wall_s": round(wall, 2), "turns_per_s": round(turns / wall, 1),
            "turn_ms": c.stats(rec["turn"]), **out}


async def probe(variant: str) -> dict:
    """One session, one turn at a time: bytes written by each append."""
    rng = random.Random(5)
    per_turn = []
    if variant == "redis_list":
        r = c.redis_client(db=2)
        await r.flushdb()
        key = "wm:probe"
        for t in range(TURNS):
            msg = message(rng)
            a = (await c.redis_info(r))["aof_current_size"]
            await r.rpush(key, msg)
            per_turn.append((await c.redis_info(r))["aof_current_size"] - a)
        await r.flushdb()
        await r.aclose()
    else:
        conn = await c.pg_conn()
        await pg_setup(conn, variant)
        for t in range(TURNS):
            msg = message(rng)
            lsn = await c.pg_wal_lsn(conn)
            if variant == "pg_jsonb":
                await conn.execute(
                    "insert into hols_wm (sid, messages) values ('probe', jsonb_build_array(%s::jsonb)) "
                    "on conflict (sid) do update set messages = hols_wm.messages || excluded.messages", (msg,))
            else:
                await conn.execute("insert into hols_wm_msgs (sid, seq, msg) values ('probe', %s, %s::jsonb)", (t, msg))
            per_turn.append(await c.pg_wal_bytes(conn, lsn))
        await conn.execute("drop table if exists hols_wm, hols_wm_msgs")
        await conn.close()
    return {"bytes_by_turn": per_turn}


async def main_async(a) -> None:
    if a.probe:
        res = await probe(a.variant)
        c.emit({"kind": "hotstate_probe", "variant": a.variant, **res})
    else:
        res = await run(a.variant, a.concurrency, a.sessions_per_worker)
        c.emit({"kind": "hotstate", "variant": a.variant, **res})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["redis_list", "pg_jsonb", "pg_rows"], required=True)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--sessions-per-worker", type=int, default=20)
    ap.add_argument("--probe", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
