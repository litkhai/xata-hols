"""LangGraph agent sessions against one persistence layer: checkpoints + long-term memory.

The graph is the same for both systems; only the checkpointer and the store change:

  postgres  AsyncPostgresSaver + AsyncPostgresStore (pgvector HNSW)
  redis     AsyncRedisSaver    + AsyncRedisStore    (Redis Query Engine HNSW)

The "model" is a node that sleeps a think time and answers a fixed text: no LLM is called.

  agentmem.py setup    --system S            tables/indexes, preload 1,000 users x 50 memories
  agentmem.py recall   --system S            recall@5/@10 of the store's search vs exact top-k
  agentmem.py run      --system S --think {0,real} --concurrency C [--label L]
  agentmem.py killwrite --system S            write until the server dies, then count lost acks
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
import uuid
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

import common as c
import vectors as v

TURNS = 16
RECALL_K = 5
REMEMBER_EVERY = 4
HUMAN_TEXT = ("Please look at the latest order export for this account, compare it with last week, "
              "and tell me which line items changed and why that might be. Keep it short.")  # ~200 chars
AI_TEXT = ("Compared with last week, three line items changed. " * 12)[:600]  # 600 chars

PG_INDEX = {
    "dims": v.DIMS,
    "embed": v.embed,
    "fields": ["text"],
    "distance_type": "cosine",
    "ann_index_config": {"kind": "hnsw", "m": 16, "ef_construction": 64},
}
REDIS_INDEX = {
    "dims": v.DIMS,
    "embed": v.embed,
    "fields": ["text"],
    "distance_type": "cosine",
    # redisvl defaults are ef_construction 200, ef_runtime 10; match pgvector's 64 / ef_search 40
    "ann_index_config": {"algorithm": "hnsw", "m": 16, "ef_construction": 64, "ef_runtime": 40},
}
PG_POOL_PER_PROC = int(os.environ.get("PG_POOL_PER_PROC", "6"))
PG_TABLES = ["checkpoints", "checkpoint_blobs", "checkpoint_writes", "store", "store_vectors"]


# --- construction ------------------------------------------------------------------------

class Backend:
    """Saver + store for one process. Use as an async context manager."""

    def __init__(self, system: str, pool_size: int = 4):
        self.system, self.pool_size = system, pool_size

    async def __aenter__(self):
        if self.system == "postgres":
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres.aio import AsyncPostgresStore

            self._pool_cm = c.pg_pool(self.pool_size)
            self.pool = await self._pool_cm.__aenter__()
            self.saver = AsyncPostgresSaver(conn=self.pool)
            self.store = AsyncPostgresStore(conn=self.pool, index=PG_INDEX)
        else:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver
            from langgraph.store.redis.aio import AsyncRedisStore

            self.saver = AsyncRedisSaver(redis_url=c.REDIS_URL)
            await self.saver.asetup()
            self.store = AsyncRedisStore(redis_url=c.REDIS_URL, index=REDIS_INDEX)
            await self.store.setup()
        return self

    async def __aexit__(self, *exc):
        if self.system == "postgres":
            await self._pool_cm.__aexit__(*exc)
        else:
            for obj in (self.store, self.saver):
                close = getattr(obj, "aclose", None) or getattr(getattr(obj, "_redis", None), "aclose", None)
                if close:
                    try:
                        await close()
                    except Exception:
                        pass


def build_graph(saver, store, rec, think_fn, last_think):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    async def agent(state, config):
        conf = config["configurable"]
        user, sid = conf["user_id"], conf["thread_id"]
        turn = sum(1 for m in state["messages"] if m.type == "human")
        t = c.now()
        await store.asearch(("memories", user), query=f"q:{user}:{sid}-{turn}", limit=RECALL_K)
        rec["store_search"].append((c.now() - t) * 1000)
        th = think_fn()
        last_think[sid] = th
        if th:
            await asyncio.sleep(th)
        if turn % REMEMBER_EVERY == 0:
            t = c.now()
            await store.aput(("memories", user), f"{sid}-{turn}", {"text": f"s:{user}:{sid}:{turn}"})
            rec["store_put"].append((c.now() - t) * 1000)
        return {"messages": [AIMessage(content=AI_TEXT)]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=saver)


def instrument(saver, rec, acks=None):
    """Time the checkpointer calls LangGraph makes; optionally record acknowledged checkpoints."""
    for name in ("aput", "aput_writes", "aget_tuple"):
        orig = getattr(saver, name)

        async def timed(*a, __orig=orig, __name=name, **kw):
            t = c.now()
            out = await __orig(*a, **kw)
            rec[f"cp_{__name}"].append((c.now() - t) * 1000)
            if acks is not None and __name == "aput":
                conf = out["configurable"]
                acks.append((conf["thread_id"], conf.get("checkpoint_ns", ""), conf["checkpoint_id"], time.time()))
            return out

        setattr(saver, name, timed)


# --- setup / preload / recall ------------------------------------------------------------

async def setup(system: str) -> None:
    from langgraph.store.base import PutOp

    async with Backend(system, pool_size=8) as b:
        if system == "postgres":
            await b.saver.setup()
            await b.store.setup()
        have = await count_preloaded(b)
        c.log(f"{system}: {have} preloaded memories")
        if have < v.USERS * v.PER_USER:
            t = c.now()
            sem = asyncio.Semaphore(8)

            async def load_user(u):
                user = v.user_id(u)
                ops = [PutOp(("memories", user), f"m{i}", {"text": f"m:{user}:{i}"}) for i in range(v.PER_USER)]
                async with sem:
                    await b.store.abatch(ops)

            await asyncio.gather(*(load_user(u) for u in range(v.USERS)))
            c.log(f"{system}: preloaded {v.USERS * v.PER_USER} memories in {c.now() - t:.1f}s")
        c.emit({"kind": "setup", "system": system, "memories": await count_preloaded(b)})


async def count_preloaded(b) -> int:
    if b.system == "postgres":
        async with b.pool.connection() as conn:
            cur = await conn.execute("select count(*) as n from store where prefix like 'memories.u%%' and key like 'm%%'")
            return int((await cur.fetchone())["n"])
    r = c.redis_client()
    try:
        n = 0
        async for _ in r.scan_iter(match="store:*", count=5000):
            n += 1
        return n
    finally:
        await r.aclose()


async def recall(system: str, queries: int) -> None:
    rng = random.Random(7)
    hits = {5: 0, 10: 0}
    lat = []
    async with Backend(system, pool_size=2) as b:
        for j in range(queries):
            user = v.user_id(rng.randrange(v.USERS))
            q = f"q:{user}:r{j}"
            t = c.now()
            res = await b.store.asearch(("memories", user), query=q, limit=10)
            lat.append((c.now() - t) * 1000)
            # only preloaded memories have ground truth; session-written ones are removed after each run
            got = [r_.value["text"] for r_ in res]
            for k in hits:
                truth = set(v.exact_top_k(user, q, k))
                hits[k] += len(truth & set(got[:k]))
        extra = {}
        if system == "postgres":
            async with b.pool.connection() as conn:
                cur = await conn.execute("select current_setting('hnsw.ef_search', true) as ef")
                extra["hnsw.ef_search"] = (await cur.fetchone())["ef"]
    c.emit({
        "kind": "recall", "system": system, "queries": queries,
        "recall_at_5": round(hits[5] / (5 * queries), 4),
        "recall_at_10": round(hits[10] / (10 * queries), 4),
        "search_ms": c.stats(lat), **extra,
    })


# --- sessions ----------------------------------------------------------------------------

def _child(args: dict) -> dict:
    return asyncio.run(_child_main(**args))


async def _child_main(system, workers, think, sessions_per_worker, seed, proc) -> dict:
    from langchain_core.messages import HumanMessage

    rec = defaultdict(list)
    last_think: dict[str, float] = {}
    rng = random.Random(seed)
    think_fn = (lambda: 0.0) if think == "0" else (lambda: rng.uniform(1.0, 3.0))
    threads: list[tuple[str, str]] = []
    errors = 0
    cpu0, t0 = time.process_time(), c.now()
    first_turn, last_turn = None, None
    # at most PG_POOL_PER_PROC connections per process (6 x 6 = 36 in all): the gateway's default
    # endpoint goes straight to Postgres, and xata.micro has max_connections = 50
    async with Backend(system, pool_size=min(workers + 2, PG_POOL_PER_PROC)) as b:
        instrument(b.saver, rec)
        graph = build_graph(b.saver, b.store, rec, think_fn, last_think)

        async def worker(w):
            nonlocal errors, first_turn, last_turn
            wr = random.Random(seed * 1000 + w)
            for s in range(sessions_per_worker):
                user = v.user_id(wr.randrange(v.USERS))
                sid = f"t{proc}x{w}x{s}x{uuid.uuid4().hex[:8]}"
                threads.append((sid, user))
                cfg = {"configurable": {"thread_id": sid, "user_id": user}}
                try:
                    t = c.now()
                    await graph.aget_state(cfg)
                    rec["load_state"].append((c.now() - t) * 1000)
                    for _ in range(TURNS):
                        t = c.now()
                        first_turn = first_turn or time.time()
                        await graph.ainvoke({"messages": [HumanMessage(content=HUMAN_TEXT)]}, cfg)
                        rec["turn_overhead"].append((c.now() - t - last_think.get(sid, 0.0)) * 1000)
                        last_turn = time.time()
                except Exception as e:  # recorded, not hidden
                    errors += 1
                    c.log(f"session {sid} failed: {type(e).__name__}: {e}")

        await asyncio.gather(*(worker(w) for w in range(workers)))
    return {"rec": dict(rec), "threads": threads, "errors": errors, "first_turn": first_turn,
            "last_turn": last_turn, "cpu_s": time.process_time() - cpu0, "wall_s": c.now() - t0}


async def server_snapshot(system: str) -> dict:
    if system == "postgres":
        conn = await c.pg_conn()
        try:
            return {"rel_bytes": await c.pg_rel_bytes(conn, PG_TABLES), "lsn": await c.pg_wal_lsn(conn),
                    "settings": await c.pg_settings(conn)}
        finally:
            await conn.close()
    r = c.redis_client()
    try:
        return {"info": await c.redis_info(r), "dbsize": await r.dbsize(),
                "config": {k: (v_.decode() if isinstance(v_, bytes) else v_)
                           for k, v_ in (await r.config_get("append*")).items()}}
    finally:
        await r.aclose()


async def cleanup(system: str, threads: list[tuple[str, str]]) -> None:
    async with Backend(system, pool_size=8) as b:
        sem = asyncio.Semaphore(8)

        async def one(sid, user):
            async with sem:
                await b.saver.adelete_thread(sid)
                for turn in range(REMEMBER_EVERY, TURNS + 1, REMEMBER_EVERY):
                    await b.store.adelete(("memories", user), f"{sid}-{turn}")

        await asyncio.gather(*(one(s, u) for s, u in threads))


def run(system: str, think: str, concurrency: int, label: str, procs: int) -> None:
    procs = max(1, min(concurrency, procs))
    per = [concurrency // procs + (1 if i < concurrency % procs else 0) for i in range(procs)]
    # think 0: 30 sessions at C=1, else 10 per worker; think 1-3 s: two sessions per worker (~65 s)
    spw = (30 if concurrency == 1 else 10) if think == "0" else 2
    before = asyncio.run(server_snapshot(system))
    t0 = c.now()
    with ProcessPoolExecutor(max_workers=procs, mp_context=mp.get_context("spawn")) as ex:
        outs = list(ex.map(_child, [
            {"system": system, "workers": per[i], "think": think, "sessions_per_worker": spw,
             "seed": 1000 + i, "proc": i} for i in range(procs)]))
    wall = c.now() - t0
    after = asyncio.run(server_snapshot(system))
    rec = defaultdict(list)
    threads = []
    for o in outs:
        for k, vals in o["rec"].items():
            rec[k].extend(vals)
        threads.extend(o["threads"])
    sessions = len(threads)
    turns = len(rec["turn_overhead"])
    # throughput over the window in which turns ran: process start-up and connecting are left out
    window = max(o["last_turn"] or 0 for o in outs) - min(o["first_turn"] or 1e12 for o in outs)
    out = {
        "kind": "sessions", "system": system, "path": label, "think": think,
        "concurrency": concurrency, "procs": procs, "sessions": sessions, "turns": turns,
        "errors": sum(o["errors"] for o in outs), "wall_s": round(wall, 2),
        "turns_per_s": round(turns / window, 2), "active_s": round(window, 2),
        "client_cpu_cores": round(sum(o["cpu_s"] for o in outs) / wall, 2),
        "ops": {k: c.stats(vals) for k, vals in sorted(rec.items())},
    }
    if system == "postgres":
        grow = {t: after["rel_bytes"][t] - before["rel_bytes"][t] for t in PG_TABLES}
        out["bytes_per_session"] = round(sum(grow.values()) / max(sessions, 1))
        out["rel_bytes_growth"] = grow
        out["settings"] = after["settings"]
        conn_lsn = before["lsn"]

        async def wal():
            conn = await c.pg_conn()
            try:
                return await c.pg_wal_bytes(conn, conn_lsn)
            finally:
                await conn.close()

        out["wal_bytes_per_turn"] = round(asyncio.run(wal()) / max(turns, 1))
    else:
        out["bytes_per_session"] = round((after["info"]["used_memory"] - before["info"]["used_memory"]) / max(sessions, 1))
        out["used_memory_after"] = after["info"]["used_memory"]
        out["aof_bytes_per_turn"] = round((after["info"]["aof_current_size"] - before["info"]["aof_current_size"]) / max(turns, 1))
        out["redis_version"] = after["info"]["redis_version"]
        out["config"] = after["config"]
    out["client_cpu_ms_per_turn"] = round(sum(o["cpu_s"] for o in outs) * 1000 / max(turns, 1), 2)
    out["client_bound"] = out["client_cpu_cores"] >= 0.85 * procs
    c.emit(out)
    # cleanup is a separate command, so run.sh can read server CPU before it starts
    with open(THREADS_FILE, "w") as f:
        json.dump(threads, f)


THREADS_FILE = "/tmp/agentmem-threads.json"


def cleanup_last(system: str) -> None:
    with open(THREADS_FILE) as f:
        threads = [tuple(t) for t in json.load(f)]
    asyncio.run(cleanup(system, threads))
    if system == "redis":
        asyncio.run(_compact())


async def _compact() -> None:
    r = c.redis_client()
    try:
        await c.redis_compact_aof(r)
    finally:
        await r.aclose()


# --- crash test --------------------------------------------------------------------------

async def killwrite(system: str, concurrency: int, max_s: float) -> None:
    """Write checkpoints with think time 0 until the server is killed from outside (run.sh),
    then wait for it to come back and check every checkpoint whose write was acknowledged."""
    from langchain_core.messages import HumanMessage

    rec = defaultdict(list)
    acks: list[tuple[str, str, str, float]] = []
    first_error = None
    t_start = time.time()
    async with Backend(system, pool_size=concurrency + 2) as b:
        instrument(b.saver, rec, acks)
        graph = build_graph(b.saver, b.store, rec, lambda: 0.0, {})
        tasks: list[asyncio.Task] = []

        async def worker(w):
            nonlocal first_error
            s = 0
            while time.time() - t_start < max_s:
                sid = f"k{w}x{s}x{uuid.uuid4().hex[:8]}"
                cfg = {"configurable": {"thread_id": sid, "user_id": v.user_id(w)}}
                try:
                    for _ in range(TURNS):
                        await graph.ainvoke({"messages": [HumanMessage(content=HUMAN_TEXT)]}, cfg)
                except Exception as e:
                    if first_error is None:
                        first_error = (time.time(), f"{type(e).__name__}: {str(e)[:200]}")
                        # stop every writer now: one that waits in the pool would otherwise carry on
                        # after the server is back, and its writes would count as "acked before the crash"
                        for t in tasks:
                            if t is not asyncio.current_task():
                                t.cancel()
                    return
                s += 1

        print("WRITING", flush=True)  # run.sh waits for this line before it starts the clock
        tasks.extend(asyncio.create_task(worker(w)) for w in range(concurrency))
        await asyncio.gather(*tasks, return_exceptions=True)
    if not first_error:
        c.emit({"kind": "kill_dry", "system": system, "acked_checkpoints": len(acks),
                "unique": len(set(acks)), "seconds": round(time.time() - t_start, 1),
                "turns": len(rec["store_search"]), "error": "server was not killed within max_s"})
        return
    # wait for the server to accept writes again
    t_back = None
    for _ in range(1200):
        try:
            if system == "postgres":
                await c.pg_one("select 1")
            else:
                r = c.redis_client()
                try:
                    await r.ping()
                    if int((await r.info("persistence")).get("loading", 0)):
                        raise RuntimeError("loading")
                finally:
                    await r.aclose()
            t_back = time.time()
            break
        except Exception:
            await asyncio.sleep(0.25)
    lost = []
    before = [a for a in acks if a[3] <= first_error[0]]
    async with Backend(system, pool_size=4) as b:
        for tid, ns, cid, _ in before:
            tup = await b.saver.aget_tuple({"configurable": {"thread_id": tid, "checkpoint_ns": ns, "checkpoint_id": cid}})
            if tup is None:
                lost.append(cid)
        threads = sorted({a[0] for a in acks})
        for tid in threads:
            await b.saver.adelete_thread(tid)
    snap = await server_snapshot(system)
    c.emit({
        "kind": "kill", "system": system, "concurrency": concurrency,
        "acked_checkpoints": len(before), "lost_checkpoints": len(lost),
        "last_ack_to_error_ms": round((first_error[0] - before[-1][3]) * 1000, 1) if before else None,
        "first_error_utc": time.strftime("%H:%M:%S", time.gmtime(first_error[0])),
        "first_error": first_error[1],
        "seconds_to_accept_again": round(t_back - first_error[0], 2) if t_back else None,
        "write_rate_per_s": round(len(before) / max(first_error[0] - before[0][3], 0.001), 1) if before else None,
        **({"settings": snap["settings"]} if system == "postgres" else {"config": snap["config"]}),
    })


# --- footprint ---------------------------------------------------------------------------

async def _sizes(system: str) -> dict:
    if system == "postgres":
        conn = await c.pg_conn()
        try:
            return await c.pg_rel_bytes(conn, PG_TABLES)
        finally:
            await conn.close()
    r = c.redis_client()
    try:
        by = defaultdict(int)
        keys = [k async for k in r.scan_iter(count=5000)]
        for i in range(0, len(keys), 500):
            p = r.pipeline(transaction=False)
            for k in keys[i:i + 500]:
                p.memory_usage(k, samples=0)
            for k, n in zip(keys[i:i + 500], await p.execute()):
                by[k.decode().split(":", 1)[0]] += n or 0
        by["used_memory"] = (await c.redis_info(r))["used_memory"]
        return dict(by)
    finally:
        await r.aclose()


async def footprint(system: str, label: str, sessions: int = 100) -> None:
    """Bytes held per finished session, from a compacted start: VACUUM FULL on the checkpoint tables
    (Postgres: disk, indexes included), or a rewritten AOF (Redis: RAM, by key prefix)."""
    from langchain_core.messages import HumanMessage

    if system == "postgres":
        conn = await c.pg_conn()
        try:
            for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                await conn.execute(f"vacuum full {t}")
            await conn.execute("vacuum store, store_vectors")
        finally:
            await conn.close()
    else:
        await _compact()
    before = await _sizes(system)
    rec = defaultdict(list)
    threads = []
    async with Backend(system, pool_size=6) as b:
        graph = build_graph(b.saver, b.store, rec, lambda: 0.0, {})
        sem = asyncio.Semaphore(10)

        async def one(i):
            user = v.user_id(i % v.USERS)
            sid = f"f{i}x{uuid.uuid4().hex[:8]}"
            threads.append((sid, user))
            cfg = {"configurable": {"thread_id": sid, "user_id": user}}
            async with sem:
                for _ in range(TURNS):
                    await graph.ainvoke({"messages": [HumanMessage(content=HUMAN_TEXT)]}, cfg)

        await asyncio.gather(*(one(i) for i in range(sessions)))
    after = await _sizes(system)
    growth = {k: after.get(k, 0) - before.get(k, 0) for k in sorted(set(before) | set(after))}
    total = growth.pop("used_memory", None) if system == "redis" else sum(growth.values())
    c.emit({"kind": "footprint", "system": system, "path": label, "sessions": sessions, "turns": TURNS,
            "bytes_per_session": round(total / sessions),
            "by_part_per_session": {k: round(n / sessions) for k, n in growth.items() if n}})
    await cleanup(system, threads)
    if system == "redis":
        await _compact()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "recall", "run", "cleanup", "footprint", "killwrite"])
    ap.add_argument("--system", choices=["postgres", "redis"], required=True)
    ap.add_argument("--think", choices=["0", "real"], default="0")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--label", default="")
    ap.add_argument("--procs", type=int, default=int(os.environ.get("BENCH_PROCS", "6")))
    ap.add_argument("--queries", type=int, default=500)
    ap.add_argument("--max-s", type=float, default=90)
    a = ap.parse_args()
    label = a.label or ("redis" if a.system == "redis" else "pg")
    if a.cmd == "setup":
        asyncio.run(setup(a.system))
    elif a.cmd == "recall":
        asyncio.run(recall(a.system, a.queries))
    elif a.cmd == "run":
        run(a.system, a.think, a.concurrency, label, a.procs)
    elif a.cmd == "cleanup":
        cleanup_last(a.system)
    elif a.cmd == "footprint":
        asyncio.run(footprint(a.system, label))
    else:
        asyncio.run(killwrite(a.system, a.concurrency, a.max_s))


if __name__ == "__main__":
    main()
