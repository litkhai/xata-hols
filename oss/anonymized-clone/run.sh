#!/usr/bin/env bash
# S10: copy a plain Postgres (a local container standing in for an external database)
# into a new root branch with `xata clone start`, replacing the personal columns on the way.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
export PROJECT=${PROJECT:-$(project_id hols)}
A=(--organization "$XATA_ORG" --project "$PROJECT")
b=lab-anon
fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; else echo "FAIL $1: got '$2', want '$3'"; fail=1; fi; }

# Source: postgres:18.6 on 127.0.0.1:5433 with the same seed as `base`. Its password is the
# image default and the port is bound to loopback only.
SRC_IMAGE=postgres:18.6@sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4
docker rm -f xata-hols-source >/dev/null 2>&1
docker run -d --name xata-hols-source -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:5433:5432 "$SRC_IMAGE" >/dev/null
until docker exec xata-hols-source pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done; sleep 2
docker exec -i xata-hols-source psql -U postgres -q -v ON_ERROR_STOP=1 < "$HERE/../platform/seed-app.sql"
src() { docker exec xata-hols-source psql -U postgres -At -c "$1"; }

"$XATA" branch create "${A[@]}" --region "$XATA_REGION" --name "$b" --no-parent --storage 10 \
  --postgres-version postgres:18.6 --instance-type "$XATA_INSTANCE" --replicas 0 --scale-to-zero false --json >/dev/null 2>&1
wait_ready "$b" || { echo "FAIL $b not ready"; exit 1; }

# `clone start` reads its rules from .xata/clone.yaml in the working directory
# (`--config` replaces the whole pgstream config instead).
work=$(mktemp -d); mkdir -p "$work/.xata"; cp "$HERE/clone.yaml" "$work/.xata/clone.yaml"
t0=$(ts)
(cd "$work" && "$XATA" clone start --source-url 'postgres://postgres:postgres@localhost:5433/postgres?sslmode=disable' \
   --validation-mode relaxed "${A[@]}" --branch "$b" --log-format json --log-level warn > "$STATE/clone.log" 2>&1) \
  || { echo "FAIL clone start (log: $STATE/clone.log)"; exit 1; }
echo "INFO clone took $(elapsed "$t0" "$(ts)")s; warnings: $(grep -c '"level":"warn"' "$STATE/clone.log")"

check "S10 users copied" "$(q "$b" "select count(*) from users")" "$(src "select count(*) from users")"
check "S10 orders copied" "$(q "$b" "select count(*) from orders")" "$(src "select count(*) from orders")"
check "S10 no email left as it was" "$(q "$b" "select count(*) from users where email = 'user' || id || '@example.com'")" 0
check "S10 email domain kept" "$(q "$b" "select count(*) from users where email not like '%@example.com'")" 0
# Same expressions as seed-app.sql, so this recomputes each row's original name and phone.
check "S10 no name left as it was" "$(q "$b" "select count(*) from users where full_name = (array['Kim','Lee','Park','Choi','Jung'])[1 + id % 5] || ' ' || (array['Minjun','Seoyeon','Jiho','Hayun','Doyun'])[1 + (id / 5) % 5]")" 0
check "S10 no phone left as it was" "$(q "$b" "select count(*) from users where phone = '010-' || lpad((id % 10000)::text, 4, '0') || '-' || lpad(((id * 7) % 10000)::text, 4, '0')")" 0
check "S10 emails still unique" "$(q "$b" "select count(distinct email) from users")" 100000
check "S10 order amounts untouched" "$(q "$b" "select sum(amount) from orders")" "$(src "select sum(amount) from orders")"
check "S10 foreign keys intact" "$(q "$b" "select count(*) from orders o left join users u on u.id = o.user_id where u.id is null")" 0
echo "INFO id 42 before: $(src "select email || ' | ' || full_name || ' | ' || phone from users where id = 42")"
echo "INFO id 42 after:  $(q "$b" "select email || ' | ' || full_name || ' | ' || phone from users where id = 42")"

rm -rf "$work"
"$XATA" branch delete "${A[@]}" --branch "$b" --yes >/dev/null 2>&1
docker rm -f xata-hols-source >/dev/null
exit $fail
