#!/usr/bin/env python3
"""Markdown tables from results/*.jsonl (the README's tables are pasted from this output).

  report.py                 # every results file, newest record wins per configuration
  report.py results/sessions-20261105T....jsonl ...
  report.py --lang ko       # the same tables with Korean headings, for the README's 한국어 half
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GIB = 1024 ** 3


def load(paths):
    rows = []
    for p in sorted(paths):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line.startswith("{"):
                    rows.append(json.loads(line))
    return rows


def latest(rows, kind, key):
    out = {}
    for r in rows:
        if r.get("kind") == kind:
            out[key(r)] = r
    return out


def ms(op, k="p50"):
    return "–" if not op or not op.get("n") else f"{op[k]:.1f}"


def kb(b):
    return "–" if b is None else f"{b / 1024:,.1f}"


KO = {
    "### Recall (preloaded 50,000 memories, 500 queries, per-user filter)": "### Recall(미리 넣은 메모리 5만 건, 쿼리 500개, 사용자별 필터)",
    "### Sessions, think time 0 (saturation)": "### 세션, think time 0(포화)",
    "### Sessions, think time 1–3 s": "### 세션, think time 1–3초",
    "### Bytes held per finished session (100 sessions x 16 turns, compacted start)": "### 끝난 세션 하나가 차지하는 크기(세션 100개 x 16턴, 압축한 상태에서 시작)",
    "### kill -9 under load (10 writers, think time 0)": "### 부하 중 kill -9(writer 10개, think time 0)",
    "### Expiry (2 KiB random payload, TTL 60 s)": "### 만료(2 KiB 랜덤 payload, TTL 60초)",
    "### Small state rewritten every turn (16 turns, 600-char messages)": "### 매 턴 고쳐 쓰는 작은 상태(16턴, 600자 메시지)",
    "### The preloaded long-term memories (1,000 users x 50, 384 dimensions)": "### 미리 넣은 장기 메모리(사용자 1,000명 x 50건, 384차원)",
    "### Bytes written by one append, by turn (single session)": "### 덧붙이기 한 번이 쓰는 바이트, 턴별(세션 하나)",
    "System": "시스템", "Durability": "내구성", "Path": "경로", "Sessions": "세션", "Errors": "오류",
    "Turns/s": "턴/초", "Turn overhead p50 ms": "턴 오버헤드 p50 ms", "Checkpoint put p50 ms": "체크포인트 쓰기 p50 ms",
    "Memory search p50 ms": "메모리 검색 p50 ms", "Server CPU cores": "서버 CPU 코어",
    "Gateway cores (throttled)": "gateway 코어(throttling)", "Client CPU ms/turn": "클라이언트 CPU ms/턴",
    "Client-bound": "클라이언트 한계", "search p50 ms": "검색 p50 ms", "p99 ms": "p99 ms",
    "Where": "위치", "KiB per session": "세션당 KiB", "Sessions per GiB": "GiB당 세션", "Largest parts": "큰 부분",
    "Acked checkpoints": "ack된 체크포인트", "Lost": "잃은 것", "Writes/s": "쓰기/초",
    "Seconds until writable": "다시 쓸 수 있을 때까지 초", "Note": "메모",
    "Variant": "변형", "Expiry by": "만료 방식", "Write s": "쓰기 초", "MiB while live": "살아 있을 때 MiB",
    "s after expiry until gone": "만료 후 사라질 때까지 초", "WAL/AOF MiB for expiry": "만료에 쓴 WAL/AOF MiB",
    "MiB left when gone": "사라진 뒤 남은 MiB", "Dead tuples when gone": "사라진 시점 dead tuple",
    "s until autovacuum": "autovacuum까지 초", "MiB after autovacuum": "autovacuum 후 MiB",
    "Turns": "턴", "Read+append p50 ms": "읽기+덧붙이기 p50 ms", "WAL/AOF bytes per turn": "턴당 WAL/AOF 바이트",
    "Table MiB": "테이블 MiB", "Dead tuples": "dead tuple", "HOT share": "HOT 비율", "Autovacuum runs": "autovacuum 횟수",
    "Memories": "메모리", "MiB": "MiB", "KiB per memory": "메모리당 KiB", "Detail (MiB)": "내역(MiB)",
    "disk (tables + indexes)": "디스크(테이블 + 인덱스)", "RAM (used_memory)": "RAM(used_memory)",
    "disk, after VACUUM FULL": "디스크, VACUUM FULL 후", "RAM, after a restart from the AOF": "RAM, AOF에서 재시작한 뒤",
    "yes": "예", "script every 10s": "스크립트 10초마다", "TTL": "TTL", "strict": "strict", "relaxed": "relaxed",
}
LANG = "en"


def t(x):
    return KO.get(x, x) if LANG == "ko" else x


_print = print


def print(*a, **k):  # noqa: A001 - headings go through the dictionary
    _print(*[t(x.rstrip("\n")) + "\n" if isinstance(x, str) and x.startswith("### ") else x for x in a], **k)


def table(head, body):
    head = [t(h) for h in head]
    body = [[t(x) if isinstance(x, str) else x for x in row] for row in body]
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join("---" for _ in head) + "|")
    for row in body:
        print("| " + " | ".join(str(x) for x in row) + " |")
    print()


def main():
    global LANG
    args = sys.argv[1:]
    if args[:2] == ["--lang", "ko"]:
        LANG, args = "ko", args[2:]
    paths = args or glob.glob(os.path.join(HERE, "results", "*.jsonl"))
    rows = load(paths)

    rec = latest(rows, "recall", lambda r: r["system"])
    if rec:
        print("### Recall (preloaded 50,000 memories, 500 queries, per-user filter)\n")
        table(["System", "recall@5", "recall@10", "search p50 ms", "p99 ms"],
              [[s, r["recall_at_5"], r["recall_at_10"], ms(r["search_ms"]), ms(r["search_ms"], "p99")]
               for s, r in sorted(rec.items())])

    ses = latest(rows, "sessions", lambda r: (r.get("durability"), r["think"], r["concurrency"], r["path"]))
    for think, title in (("0", "think time 0 (saturation)"), ("real", "think time 1–3 s")):
        body = []
        for (d, t, conc, path), r in sorted(ses.items(), key=lambda kv: (kv[0][0] or "", kv[0][2], kv[0][3])):
            if t != think or d not in ("strict", "relaxed"):
                continue
            o = r["ops"]
            cpu = r.get("server_cpu", {})
            server = cpu.get("redis") if path == "redis" else cpu.get("postgres")
            gw = cpu.get("gateway") if path != "redis" else None
            body.append([d, conc, path, r["sessions"], r["errors"], r["turns_per_s"],
                         ms(o.get("turn_overhead")), ms(o.get("turn_overhead"), "p99"),
                         ms(o.get("cp_aput")), ms(o.get("cp_aput"), "p99"),
                         ms(o.get("store_search")), ms(o.get("store_search"), "p99"),
                         "–" if not server else server["cores"],
                         "–" if not gw else f"{gw['cores']} ({gw['throttled_s']} s)",
                         r.get("client_cpu_ms_per_turn", "–"), "yes" if r.get("client_bound") else ""])
        if body:
            print(f"### Sessions, {title}\n")
            table(["Durability", "C", "Path", "Sessions", "Errors", "Turns/s",
                   "Turn overhead p50 ms", "p99", "Checkpoint put p50 ms", "p99",
                   "Memory search p50 ms", "p99", "Server CPU cores", "Gateway cores (throttled)",
                   "Client CPU ms/turn", "Client-bound"], body)

    fp = latest(rows, "footprint", lambda r: r["system"])
    if fp:
        print("### Bytes held per finished session (100 sessions x 16 turns, compacted start)\n")
        def parts(sys_, r):
            p = sorted(r["by_part_per_session"].items(), key=lambda kv: -kv[1])[:4]
            if sys_ == "postgres":
                return ", ".join(f"{k} {n / 1024:,.1f} KiB" for k, n in p)
            # MEMORY USAGE estimates JSON documents high (its sum exceeds used_memory), so shares only
            tot = sum(r["by_part_per_session"].values())
            return ", ".join(f"{k} {100 * n / tot:.0f}%" for k, n in p)

        table(["System", "Where", "KiB per session", "Sessions per GiB", "Largest parts"],
              [[s, "disk (tables + indexes)" if s == "postgres" else "RAM (used_memory)",
                kb(r["bytes_per_session"]), f"{GIB // r['bytes_per_session']:,}", parts(s, r)]
               for s, r in sorted(fp.items())])

    kil = latest(rows, "kill", lambda r: (r.get("durability"), r["system"]))
    body = [[d, s, r.get("acked_checkpoints"), r.get("lost_checkpoints"), r.get("write_rate_per_s"),
             r.get("seconds_to_accept_again"), r.get("error", "")]
            for (d, s), r in sorted(kil.items())]
    if body:
        print("### kill -9 under load (10 writers, think time 0)\n")
        table(["Durability", "System", "Acked checkpoints", "Lost", "Writes/s", "Seconds until writable", "Note"], body)

    exp = latest(rows, "expiry", lambda r: (r["sessions"], r["variant"]))
    body = []
    for (n, v), r in sorted(exp.items()):
        cost = r.get("aof_bytes_for_expiry", r.get("wal_bytes_for_expiry"))
        left = r.get("bytes_left_after", r.get("bytes_left_when_gone"))
        body.append([f"{n:,}", v, r.get("job", "TTL"), r["write_s"], f"{r['bytes_while_live'] / 2**20:,.1f}",
                     r["seconds_after_expiry_until_gone"], f"{(cost or 0) / 2**20:,.2f}",
                     f"{(left or 0) / 2**20:,.1f}",
                     r.get("dead_tuples_when_gone", "–"),
                     r.get("seconds_until_autovacuum", "–"),
                     "–" if r.get("bytes_after_autovacuum") is None else f"{r['bytes_after_autovacuum'] / 2**20:,.1f}"])
    if body:
        print("### Expiry (2 KiB random payload, TTL 60 s)\n")
        table(["Sessions", "Variant", "Expiry by", "Write s", "MiB while live", "s after expiry until gone",
               "WAL/AOF MiB for expiry", "MiB left when gone", "Dead tuples when gone",
               "s until autovacuum", "MiB after autovacuum"], body)

    hot = latest(rows, "hotstate", lambda r: (r["variant"], r["concurrency"]))
    body = []
    for (v, conc), r in sorted(hot.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        size = r.get("table_bytes")
        body.append([conc, v, r["turns"], r["turns_per_s"], ms(r["turn_ms"]), ms(r["turn_ms"], "p99"),
                     r.get("wal_bytes_per_turn", r.get("aof_bytes_per_turn")),
                     "–" if size is None else f"{size / 2**20:,.1f}",
                     r.get("dead_tuples", "–"), r.get("hot_update_share") or "–", r.get("autovacuum_runs", "–"),
                     "yes" if r.get("client_bound") else ""])
    if body:
        print("### Small state rewritten every turn (16 turns, 600-char messages)\n")
        table(["C", "Variant", "Turns", "Turns/s", "Read+append p50 ms", "p99",
               "WAL/AOF bytes per turn", "Table MiB", "Dead tuples", "HOT share", "Autovacuum runs",
               "Client-bound"], body)

    msz = latest(rows, "memsize", lambda r: r["system"])
    if msz:
        print("### The preloaded long-term memories (1,000 users x 50, 384 dimensions)\n")
        body = []
        for s_, r in sorted(msz.items()):
            if s_ == "postgres":
                t = r["tables"]
                detail = (f"store_vectors heap {t['store_vectors']['heap'] / 2**20:,.1f} + indexes "
                          f"{t['store_vectors']['indexes'] / 2**20:,.1f}; store {(t['store']['heap'] + t['store']['indexes']) / 2**20:,.1f}")
                where = "disk, after VACUUM FULL"
            else:
                detail = f"used_memory_dataset {r['used_memory_dataset'] / 2**20:,.1f}"
                where = "RAM, after a restart from the AOF"
            body.append([s_, r["memories"], where, f"{r['bytes'] / 2**20:,.1f}",
                         f"{r['bytes'] / r['memories'] / 1024:,.2f}", detail])
        table(["System", "Memories", "Where", "MiB", "KiB per memory", "Detail (MiB)"], body)

    pr = latest(rows, "hotstate_probe", lambda r: r["variant"])
    if pr:
        print("### Bytes written by one append, by turn (single session)\n")
        turns = [1, 2, 4, 8, 12, 16]
        table(["Variant"] + [f"turn {t}" for t in turns],
              [[v] + [r["bytes_by_turn"][t - 1] for t in turns] for v, r in sorted(pr.items())])


if __name__ == "__main__":
    main()
