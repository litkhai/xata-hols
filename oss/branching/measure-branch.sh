#!/usr/bin/env bash
# Create a child branch and time each phase: API call, volume snapshot ready,
# child PVC bound, branch ready, first query. Prints one JSON line.
#
#   measure-branch.sh <child> [parent=main]
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
PROJECT=${PROJECT:-$(project_id hols)}
child=${1:?child branch name}; parent=${2:-main}
pid=$(branch_k8s_id "$parent")
size=$(q "$parent" "select pg_database_size(current_database())")

t0=$(ts)
"$XATA" branch create --organization "$XATA_ORG" --project "$PROJECT" --region "$XATA_REGION" \
  --name "$child" --parent-branch "$parent" --instance-type "$XATA_INSTANCE" --replicas 0 --json > "$STATE/$child.create.json" 2>"$STATE/$child.err"
t_api=$(ts)
cid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$STATE/$child.create.json")

t_snap=""; t_pvc=""
until [ -n "$t_snap" ] && [ -n "$t_pvc" ]; do
  [ -z "$t_snap" ] && [ "$(kubectl -n xata-clusters get volumesnapshot "$pid-$cid" -o jsonpath='{.status.readyToUse}' 2>/dev/null)" = true ] && t_snap=$(ts)
  [ -z "$t_pvc" ] && [ "$(kubectl -n xata-clusters get pvc "$cid-1" -o jsonpath='{.status.phase}' 2>/dev/null)" = Bound ] && t_pvc=$(ts)
  sleep 0.5
done
wait_ready "$child" 2>>"$STATE/$child.err"
t_ready=$(ts)
q "$child" "select 1" > /dev/null
t_query=$(ts)

printf '{"child":"%s","parent":"%s","parent_db_bytes":%s,"api_s":%s,"snapshot_ready_s":%s,"pvc_bound_s":%s,"branch_ready_s":%s,"first_query_s":%s}\n' \
  "$child" "$parent" "$size" "$(elapsed "$t0" "$t_api")" "$(elapsed "$t0" "$t_snap")" "$(elapsed "$t0" "$t_pvc")" \
  "$(elapsed "$t0" "$t_ready")" "$(elapsed "$t0" "$t_query")"
