"""Size of the preloaded long-term memories (1,000 users x 50), on a compacted store.

  memsize.py prune --system S    delete memories that are not preloaded ones (left by aborted runs)
  memsize.py size  --system S    Postgres: VACUUM FULL, then table + index bytes (disk);
                                 Redis: used_memory and the store's share by key prefix (RAM;
                                 run after a restart from the AOF, see run.sh memsize)
"""
from __future__ import annotations

import argparse
import asyncio
import json

import common as c


async def prune(system: str) -> int:
    if system == "postgres":
        conn = await c.pg_conn()
        try:
            n = 0
            for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await conn.execute(f"delete from {t}")  # no session is running: all left over
            for t in ("store_vectors", "store"):
                cur = await conn.execute(f"delete from {t} where prefix like 'memories.%%' and key not like 'm%%'")
                n = max(n, cur.rowcount)
            return n
        finally:
            await conn.close()
    r = c.redis_client()
    try:
        doomed = []
        async for k in r.scan_iter(match="store:*", count=5000):
            doc = await r.json().get(k)
            # RedisStore keeps the namespace escaped for the TEXT field: "memories\\.u0103"
            prefix = doc.get("prefix", "").replace("\\", "") if doc else ""
            if prefix.startswith("memories.") and not str(doc.get("key", "")).startswith("m"):
                doomed.append(k)
        for k in doomed:
            uid = k.decode().split(":", 1)[1]
            await r.delete(k, f"store_vectors:{uid}")
        # no session is running: every checkpoint key is left over from an aborted or dry run
        for pat in ("checkpoint*", "write_keys_zset*"):
            async for k in r.scan_iter(match=pat, count=5000):
                await r.delete(k)
        return len(doomed)
    finally:
        await r.aclose()


async def size(system: str) -> dict:
    if system == "postgres":
        conn = await c.pg_conn()
        try:
            await conn.execute("vacuum full store")
            await conn.execute("vacuum full store_vectors")
            cur = await conn.execute(
                "select c.relname as t, pg_table_size(c.oid) as heap, pg_indexes_size(c.oid) as idx "
                "from pg_class c where c.relname in ('store', 'store_vectors')")
            rows = await cur.fetchall()
            cur = await conn.execute("select count(*) as n from store_vectors")
            n = (await cur.fetchone())["n"]
            return {"memories": n, "tables": {r_["t"]: {"heap": r_["heap"], "indexes": r_["idx"]} for r_ in rows},
                    "bytes": sum(r_["heap"] + r_["idx"] for r_ in rows)}
        finally:
            await conn.close()
    r = c.redis_client()
    try:
        info = await r.info("memory")
        n = 0
        async for _ in r.scan_iter(match="store_vectors:*", count=5000):
            n += 1
        return {"memories": n, "used_memory": int(info["used_memory"]),
                "used_memory_dataset": int(info["used_memory_dataset"]),
                "bytes": int(info["used_memory"])}
    finally:
        await r.aclose()


async def main_async(a) -> None:
    if a.cmd == "prune":
        c.log(f"{a.system}: pruned {await prune(a.system)} non-preloaded memories")
    else:
        c.emit({"kind": "memsize", "system": a.system, **(await size(a.system))})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prune", "size"])
    ap.add_argument("--system", choices=["postgres", "redis"], required=True)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
