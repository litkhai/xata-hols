#!/usr/bin/env bash
# S3–S5: branch content, two-way isolation, and a three-level tree whose middle branch is deleted.
# Uses `base` (seeded by platform/up.sh). Every check prints PASS or FAIL; exits non-zero on a FAIL.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
export PROJECT=${PROJECT:-$(project_id hols)}
A=(--organization "$XATA_ORG" --project "$PROJECT")
fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; else echo "FAIL $1: got '$2', want '$3'"; fail=1; fi; }
rm_branch() { "$XATA" branch delete "${A[@]}" --branch "$1" --yes >/dev/null 2>&1; }
digest="select count(*)||':'||md5(string_agg(id::text||amount::text, ',' order by id)) from orders"

# S3 — a child starts as an exact copy
new_branch lab-child base || { echo "FAIL create lab-child"; exit 1; }
check "S3 child equals parent" "$(q lab-child "$digest")" "$(q base "$digest")"

# S4 — writes on either side stay on that side
q lab-child "update orders set amount = 0 where id <= 1000; create table child_only(x int);" >/dev/null
q base "create table parent_only(x int);" >/dev/null
check "S4 parent does not see child update" "$(q base "select count(*) from orders where id <= 1000 and amount = 0")" 0
check "S4 parent does not see child table" "$(q base "select to_regclass('child_only') is null")" t
check "S4 child does not see parent table" "$(q lab-child "select to_regclass('parent_only') is null")" t
q base "drop table parent_only" >/dev/null

# S5 — grandchild inherits the child's state, and survives the child's deletion
new_branch lab-grandchild lab-child || { echo "FAIL create lab-grandchild"; exit 1; }
check "S5 grandchild inherits child state" "$(q lab-grandchild "select count(*) from orders where id <= 1000 and amount = 0")" 1000
rm_branch lab-child
sleep 10
check "S5 grandchild answers after its parent is deleted" "$(q lab-grandchild "select count(*) from orders")" 300000
"$XATA" branch tree "${A[@]}" --json | python3 -c '
import json, sys
def walk(n, d=0):
    print("     " + "  " * d + n["name"]); [walk(c, d + 1) for c in n.get("children", [])]
print("INFO tree after deleting lab-child:"); [walk(r) for r in json.load(sys.stdin)]'
rm_branch lab-grandchild
exit $fail
