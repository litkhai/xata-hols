#!/usr/bin/env bash
# Every lab in order, against the platform that platform/up.sh brought up.
# The log is what a "Verified on" line in the READMEs refers to.
#
#   oss/run-all.sh            # about 15 minutes after up.sh
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/platform/env.sh"
export PROJECT=$(project_id hols)
log="$STATE/run-all-$(date -u +%Y%m%dT%H%M%SZ).log"
fail=0
run() { local shown="$*"; echo "== ${shown//$HERE/oss}" | tee -a "$log"; "$@" 2>&1 | tee -a "$log"; [ "${PIPESTATUS[0]}" -eq 0 ] || fail=1; }

{
  echo "date: $(date -u +%FT%TZ)"
  echo "xata: $(git -C "${XATA_SRC:-$STATE/xata}" rev-parse HEAD 2>/dev/null)"
  echo "cli:  $("$XATA" version --json 2>/dev/null | tr -d '\n ')"
  echo "kind: $(kind version) · k8s $(kubectl version 2>/dev/null | awk '/Server/{print $3}')"
  echo "docker memory: $(( $(docker info --format '{{.MemTotal}}') / 1024 / 1024 )) MiB"
  echo "postgres: $(q base "select version()")"
} | tee -a "$log"

run "$HERE/branching/measure-branch.sh" lab-measure base
"$XATA" branch delete --organization "$XATA_ORG" --project "$PROJECT" --branch lab-measure --yes >/dev/null 2>&1
run "$HERE/branching/run.sh"
run "$HERE/pgroll-migrations/run.sh"
run "$HERE/serverless-sql/query.sh" base "select count(*) as users from users" -w ' HTTP %{http_code} %{time_total}s'
run "$HERE/anonymized-clone/run.sh"
run "$HERE/agent-fanout/fanout.sh" 3
run env RETRY_BUSY=0 "$HERE/agent-fanout/fanout.sh" 6
run env RETRY_BUSY=5 "$HERE/agent-fanout/fanout.sh" 6
run "$HERE/scale-to-zero/run.sh"

echo "log: $log"
[ "$fail" -eq 0 ] && echo "ALL LABS PASSED" || echo "SOME LABS FAILED"
exit $fail
