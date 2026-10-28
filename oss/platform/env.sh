# Sourced by every lab script: tool paths, the project this run uses, and small helpers.
# Nothing here starts or changes the platform; platform/up.sh does that.

XATA=${XATA:-$HOME/.config/xata/bin/xata}
command -v psql >/dev/null 2>&1 || PATH="/opt/homebrew/opt/libpq/bin:$PATH"
export XATA_ORG=${XATA_ORG:-default-org}
export XATA_REGION=${XATA_REGION:-local}
export XATA_INSTANCE=${XATA_INSTANCE:-xata.micro}

# Connection strings carry the branch password: they live here (mode 600), never in the repository.
STATE=${XATA_HOLS_STATE:-$HOME/.cache/xata-hols}
mkdir -p "$STATE" && chmod 700 "$STATE"

ts() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
elapsed() { python3 -c "import sys; print(f'{float(sys.argv[2])-float(sys.argv[1]):.1f}')" "$1" "$2"; }

project_id() { "$XATA" project list --organization "$XATA_ORG" --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d.get("projects",d) if isinstance(d,dict) else d; print(next(p["id"] for p in d if p["name"]==sys.argv[1]))' "${1:-hols}"; }

# branch_url <branch> → prints nothing, writes $STATE/<branch>.url and echoes its path
branch_url() {
  local f="$STATE/$1.url"
  (umask 077; "$XATA" branch url --organization "$XATA_ORG" --project "$PROJECT" --branch "$1" > "$f")
  echo "$f"
}

# q <branch> <sql> — one query, tuples only
q() { psql "$(cat "$(branch_url "$1")")" -X -At -v ON_ERROR_STOP=1 -c "$2"; }

# wait_ready <branch> [timeout_s=600] — `xata branch wait-ready` has no timeout, and a branch
# that cannot boot waits forever, so poll the branch status with a deadline instead.
wait_ready() {
  local start; start=$(date +%s)
  until [ "$("$XATA" branch describe --organization "$XATA_ORG" --project "$PROJECT" --branch "$1" --json 2>/dev/null |
             python3 -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("statusType",""))' 2>/dev/null)" = STATUS_TYPE_HEALTHY ]; do
    [ $(( $(date +%s) - start )) -gt "${2:-600}" ] && { echo "branch $1 not healthy after ${2:-600}s" >&2; return 1; }
    sleep 3
  done
}

# new_branch <name> <parent> — child branch with the lab defaults, ready to query.
# CHECKPOINT first: the local CSI hostpath driver snapshots with tar, which is not atomic,
# and a parent with recent writes once produced a child that could not find a valid
# checkpoint record (README, Local deviations). Set NO_CHECKPOINT=1 to see the raw behaviour.
new_branch() {
  [ -n "${NO_CHECKPOINT:-}" ] || q "$2" "checkpoint" >/dev/null
  "$XATA" branch create --organization "$XATA_ORG" --project "$PROJECT" --region "$XATA_REGION" --name "$1" \
    --parent-branch "$2" --instance-type "$XATA_INSTANCE" --replicas 0 --json >/dev/null 2>"$STATE/$1.err" &&
    wait_ready "$1"
}

# k8s name of a branch (the CNPG cluster and its PVCs use it)
branch_k8s_id() { "$XATA" branch describe --organization "$XATA_ORG" --project "$PROJECT" --branch "$1" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }
