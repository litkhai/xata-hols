#!/usr/bin/env bash
# S6: a child branch (scale-to-zero on, 5 min inactivity — set by platform/up.sh) hibernates
# once its last connection closes, and the gateway wakes it on the next connection.
# Takes about 7 minutes. Prints the timeline and the wake latency.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
export PROJECT=${PROJECT:-$(project_id hols)}
A=(--organization "$XATA_ORG" --project "$PROJECT")
b=lab-s2z

new_branch "$b" base || { echo "FAIL create $b"; exit 1; }
id=$(branch_k8s_id "$b")
url=$(cat "$(branch_url "$b")")
q "$b" "select 1" >/dev/null
echo "$(date -u +%T) last query; scale-to-zero: $("$XATA" branch describe "${A[@]}" --branch "$b" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["scaleToZero"])')"

# The sidecar counts active and idle client connections once a minute and hibernates
# after the inactivity period with none (services/scale-to-zero-sidecar). Watch both.
start=$(date +%s); prev=""
while [ $(( $(date +%s) - start )) -lt 1200 ]; do
  pods=$(kubectl -n xata-clusters get pods -l "cnpg.io/cluster=$id,cnpg.io/podRole=instance" --no-headers 2>/dev/null | wc -l | tr -d ' ')
  seen=$(kubectl -n xata-clusters logs "$id-1" -c scale-to-zero --tail=1 2>/dev/null | python3 -c '
import json, sys
try: d = json.loads(sys.stdin.read()); print(d.get("count", d.get("msg", "")))
except Exception: print("-")')
  cur="pods=$pods sidecar=$seen"
  [ "$cur" != "$prev" ] && echo "$(date -u +%T) +$(( $(date +%s) - start ))s $cur"
  prev=$cur
  [ "$pods" = 0 ] && break
  sleep 10
done
[ "$pods" = 0 ] || { echo "FAIL S6 not hibernated after 20 min"; exit 1; }
echo "PASS S6 hibernated $(( $(date +%s) - start ))s after the last query"

t0=$(ts)
rows=$(PGCONNECT_TIMEOUT=120 psql "$url" -X -At -c "select count(*) from orders" 2>&1)
echo "PASS S6 first query woke the branch in $(elapsed "$t0" "$(ts)")s (rows: $rows)"
"$XATA" branch delete "${A[@]}" --branch "$b" --yes >/dev/null 2>&1
