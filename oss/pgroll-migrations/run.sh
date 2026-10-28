#!/usr/bin/env bash
# S7: a pgroll migration on a branch. While it is in progress, the old schema (`public`)
# and the new version schema both read and write; after `complete` only the new shape is
# left; the parent branch never changes.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
export PROJECT=${PROJECT:-$(project_id hols)}
A=(--organization "$XATA_ORG" --project "$PROJECT")
b=lab-mig; R=("${A[@]}" --branch "$b")
fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; else echo "FAIL $1: got '$2', want '$3'"; fail=1; fi; }
cols() { q "$1" "select string_agg(column_name, ',' order by column_name) from information_schema.columns where table_schema='$2' and table_name='users'"; }
roll() { "$XATA" roll "$@" "${R[@]}" 2>&1 | sed 's/\x1b\[[0-9;]*m//g; s/\r/\n/g' | tail -1; }

new_branch "$b" base || { echo "FAIL create $b"; exit 1; }

work=$(mktemp -d); cp "$HERE/migrations/01_display_name_email_domain.yaml" "$work/"
roll init
roll baseline 00_baseline "$work" --yes            # the seeded schema predates pgroll
t0=$(ts); roll start "$work/01_display_name_email_domain.yaml" --use-version-schema
echo "INFO start (backfill of 100000 rows) took $(elapsed "$t0" "$(ts)")s"
new=public_01_display_name_email_domain

q "$b" "insert into public.users(id, email, full_name, phone) values (200001, 'old@legacy.test', 'Old Client', '010-0000-0001')" >/dev/null
q "$b" "set search_path = $new; insert into users(id, email, display_name, phone) values (200002, 'new@modern.test', 'New Client', '010-0000-0002')" >/dev/null
check "S7 new schema sees an old-client write, column derived" "$(q "$b" "select display_name || '/' || email_domain from $new.users where id = 200001")" "Old Client/legacy.test"
check "S7 old schema sees a new-client write" "$(q "$b" "select full_name from public.users where id = 200002")" "New Client"
check "S7 existing rows backfilled" "$(q "$b" "select count(*) from $new.users where id <= 100000 and email_domain is null")" 0

t0=$(ts); roll complete; echo "INFO complete took $(elapsed "$t0" "$(ts)")s"
check "S7 public has only the new shape" "$(cols "$b" public)" "created_at,display_name,email,email_domain,id,phone"
check "S7 parent unchanged" "$(cols base public)" "created_at,email,full_name,id,phone"
rm -rf "$work"
"$XATA" branch delete "${A[@]}" --branch "$b" --yes >/dev/null 2>&1
exit $fail
