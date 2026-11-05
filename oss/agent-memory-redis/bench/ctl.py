"""Small controls run.sh calls between runs.

  ctl.py redis-durability {always,everysec}   AOF on with that fsync policy, RDB snapshots off
  ctl.py redis-flush                          empty every logical db (between systems' runs)
  ctl.py pg-sync {on,off}                     synchronous_commit for the bench role (all connections)
  ctl.py pg-idle-reset                        end the role's idle backends (PgBouncer's idle server connections)
  ctl.py pg-check                             what the bench role may use on this branch
  ctl.py pg-reset                             drop the LangGraph tables (before a fresh setup)
"""
from __future__ import annotations

import asyncio
import sys

import common as c


async def main(cmd: str, arg: str | None) -> None:
    if cmd == "redis-durability":
        r = c.redis_client()
        try:
            c.emit({"kind": "redis_config", **(await c.redis_durability(r, arg)), **(await c.redis_info(r))})
        finally:
            await r.aclose()
    elif cmd == "redis-flush":
        r = c.redis_client()
        try:
            await r.flushall()
        finally:
            await r.aclose()
    elif cmd == "pg-check":
        conn = await c.pg_conn()
        try:
            cur = await conn.execute(
                "select name, default_version, installed_version from pg_available_extensions "
                "where name in ('vector','pg_cron','pg_partman') order by name")
            ext = await cur.fetchall()
            cur = await conn.execute("select current_user as u, current_database() as d, rolsuper, rolcreaterole "
                                     "from pg_roles where rolname = current_user")
            who = await cur.fetchone()
            cur = await conn.execute("select current_setting('cron.database_name', true) as cron_db, "
                                     "current_setting('shared_preload_libraries', true) as preload")
            c.emit({"kind": "pg_check", "extensions": ext, "role": who, **(await cur.fetchone()),
                    "settings": await c.pg_settings(conn)})
        finally:
            await conn.close()
    elif cmd == "pg-sync":
        # The branch pooler runs PgBouncer in transaction mode, so a session SET would not stick to
        # the server connection a later transaction gets. Set it on the role instead, then end the
        # role's other backends so the pooler reconnects with the new default, and check.
        conn = await c.pg_conn()
        try:
            await conn.execute(f"alter role current_user set synchronous_commit = {'on' if arg == 'on' else 'off'}")
            cur = await conn.execute(
                "select count(pg_terminate_backend(pid)) as n from pg_stat_activity "
                "where usename = current_user and pid <> pg_backend_pid() and backend_type = 'client backend'")
            ended = (await cur.fetchone())["n"]
        finally:
            await conn.close()
        await asyncio.sleep(2)
        seen = set()
        async with c.pg_pool(8) as pool:
            async def probe():
                async with pool.connection() as pc:
                    cur = await pc.execute("show synchronous_commit")
                    seen.add((await cur.fetchone())["synchronous_commit"])
            await asyncio.gather(*(probe() for _ in range(32)))
        c.emit({"kind": "pg_sync", "wanted": arg, "seen": sorted(seen), "backends_ended": ended})
        if seen != {arg}:
            raise SystemExit(f"synchronous_commit not uniform: {seen}")
    elif cmd == "pg-idle-reset":
        # PgBouncer keeps idle server connections for server_idle_timeout (60 s); on the gateway's
        # default endpoint they would take Postgres slots (max_connections 50) from the next run
        conn = await c.pg_conn()
        try:
            cur = await conn.execute(
                "select count(pg_terminate_backend(pid)) as n from pg_stat_activity "
                "where usename = current_user and pid <> pg_backend_pid() and state = 'idle'")
            c.log(f"ended {(await cur.fetchone())['n']} idle backends")
        finally:
            await conn.close()
    elif cmd == "pg-reset":
        conn = await c.pg_conn()
        try:
            await conn.execute("drop table if exists checkpoints, checkpoint_blobs, checkpoint_writes, "
                               "checkpoint_migrations, store, store_vectors, store_migrations, vector_migrations cascade")
        finally:
            await conn.close()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
