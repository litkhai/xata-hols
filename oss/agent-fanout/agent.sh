#!/usr/bin/env bash
# One "agent": branch from base, change the schema the way only this agent does,
# run its test, check it cannot see any other agent's change, delete the branch.
# Prints one JSON line; exits non-zero when any step fails.
#
#   agent.sh <n> [base=base]
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
PROJECT=${PROJECT:-$(project_id hols)}
n=${1:?agent number}; base=${2:-base}; b="agent-$n"
A=(--organization "$XATA_ORG" --project "$PROJECT")
step="create"; ok=true

# Branch creation takes a per-project advisory lock without waiting
# (services/projects/branch/create.go, TryAcquireProjectLock), so concurrent creates
# in one project can fail with "project … is busy; retry later". RETRY_BUSY=N retries
# that error up to N times with a growing delay; 0 (the default) shows the raw behaviour.
retries=0
t0=$(ts)
while :; do
  ok=true
  "$XATA" branch create "${A[@]}" --region "$XATA_REGION" --name "$b" --parent-branch "$base" \
    --instance-type "$XATA_INSTANCE" --replicas 0 --json > /dev/null 2>"$STATE/$b.err" && break
  ok=false
  grep -q 'is busy; retry later' "$STATE/$b.err" && [ "$retries" -lt "${RETRY_BUSY:-0}" ] || break
  retries=$((retries + 1)); sleep $((retries * 2))
done
$ok && { step="wait-ready"; wait_ready "$b" 2>>"$STATE/$b.err" || ok=false; }
t_ready=$(ts)

# The change only this agent makes: a column named after it, filled from existing data.
$ok && { step="migrate"; q "$b" "alter table users add column agent_$n int; update users set agent_$n = id % $n where id <= 10000;" > /dev/null 2>>"$STATE/$b.err" || ok=false; }
# Its test: the column is there and filled, the seed data is intact.
$ok && { step="test"; [ "$(q "$b" "select count(agent_$n) from users")" = 10000 ] && [ "$(q "$b" "select count(*) from orders")" = 300000 ] || ok=false; }
# Isolation: no other agent's column is visible on this branch.
others=""
$ok && { step="isolation"; others=$(q "$b" "select string_agg(column_name, ' ') from information_schema.columns where table_name='users' and column_name like 'agent\_%' and column_name <> 'agent_$n'"); [ -z "$others" ] || ok=false; }
t_tested=$(ts)

"$XATA" branch delete "${A[@]}" --branch "$b" --yes > /dev/null 2>>"$STATE/$b.err" || { ok=false; step="${step}+delete"; }
t_end=$(ts)

$ok && step="done"
printf '{"agent":%s,"ok":%s,"last_step":"%s","busy_retries":%s,"ready_s":%s,"work_s":%s,"delete_s":%s,"total_s":%s,"foreign_columns":"%s"}\n' \
  "$n" "$ok" "$step" "$retries" "$(elapsed "$t0" "$t_ready")" "$(elapsed "$t_ready" "$t_tested")" "$(elapsed "$t_tested" "$t_end")" "$(elapsed "$t0" "$t_end")" "$others"
$ok
