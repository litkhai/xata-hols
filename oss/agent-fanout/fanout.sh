#!/usr/bin/env bash
# Run N agents at once against the same base branch and summarise.
#
#   fanout.sh <N> [base=base]
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
export PROJECT=${PROJECT:-$(project_id hols)}
N=${1:?number of agents}; base=${2:-base}
out="$STATE/fanout-$N-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
before=$(q "$base" "select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_name='users'")

t0=$(ts)
for i in $(seq 1 "$N"); do "$HERE/agent.sh" "$i" "$base" >> "$out" & done
wait
wall=$(elapsed "$t0" "$(ts)")
after=$(q "$base" "select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_name='users'")

cat "$out"
python3 - "$out" "$N" "$wall" "$([ "$before" = "$after" ] && echo true || echo false)" <<'PY'
import json, statistics, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
ok = [r for r in rows if r["ok"]]
ready = sorted(r["ready_s"] for r in ok)
print(json.dumps({
    "agents": int(sys.argv[2]), "succeeded": len(ok), "wall_s": float(sys.argv[3]),
    "ready_s_median": statistics.median(ready) if ready else None, "ready_s_max": max(ready) if ready else None,
    "base_schema_unchanged": sys.argv[4] == "true",
}))
PY
