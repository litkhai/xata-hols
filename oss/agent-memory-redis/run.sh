#!/usr/bin/env bash
# Agent memory on Xata vs Redis (issue #10): LangGraph checkpoints and long-term memory,
# expiry, and small state rewritten every turn. Needs the platform from ../platform/up.sh.
#
#   run.sh               # everything: about 90 minutes
#   run.sh up            # the agentmem branch, Redis, the bench pod
#   run.sh setup         # LangGraph tables, preload 50,000 memories on both, recall check, footprint
#   run.sh sessions      # the 24-run session matrix + 6 runs on the gateway's pooler endpoint
#   run.sh kill          # kill -9 under load, 4 runs
#   run.sh expiry        # 6 runs
#   run.sh hotstate      # 6 runs + 3 single-session probes
#   run.sh memsize       # the preloaded memories' size on each, compacted
#   run.sh down          # remove Redis, the bench pod and the agentmem branch; gateway back to the local overlay
#
# Every result is one JSON line appended to results/<step>-<UTC>.jsonl.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
PROJECT=$(project_id hols)
export PROJECT
A=(--organization "$XATA_ORG" --project "$PROJECT")
NS=hols-bench
BRANCH=${AGENTMEM_BRANCH:-agentmem}
NODE=${KIND_NODE:-kind-control-plane}
IMAGE=hols-agentmem-bench:local
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$HERE/results"

say() { echo "== $(date -u +%H:%M:%S) $*" >&2; }
bench() { kubectl -n "$NS" exec bench -- "$@"; }
# py <PG path: gateway|gateway_pooler> <script> [args] — PG_URL is chosen inside the pod, never on this command line
py() { local path=$1; shift; bench sh -c "PG_URL=\"\$PG_URL_$(echo "$path" | tr a-z A-Z)\" exec python \"\$@\"" sh "$@"; }
record() { tee -a "$HERE/results/$1-$STAMP.jsonl"; }

# The local overlay shrinks the SQL gateway to fit a laptop (kustomize/overlays/local/kustomization.yaml:
# GOMAXPROCS 1, CPU limit 200m); the gateway chart ships GOMAXPROCS 8, CPU limit 8 and request 4
# (charts/gateway). At 200m every Postgres number here would measure the proxy, so this lab runs
# with the chart's values and puts the overlay's back on `down` (README, Local deviations).
gateway_resources() {  # gateway_resources <chart|local>
  local procs res
  if [ "$1" = chart ]; then procs=8; res='{"limits":{"cpu":"8"},"requests":{"cpu":"4","memory":"512Mi"}}'
  else procs=1; res='{"limits":{"cpu":"200m","memory":"100Mi"},"requests":{"cpu":"100m","memory":"100Mi"}}'; fi
  kubectl -n xata patch deploy gateway --type json -p "[
    {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/env/0\",\"value\":{\"name\":\"GOMAXPROCS\",\"value\":\"$procs\"}},
    {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources\",\"value\":$res}]" >/dev/null
  kubectl -n xata rollout status deploy/gateway --timeout=600s >/dev/null
}

# pod_cpu <ns> <label selector> → "<usage_usec> <throttled_usec>" from the pod's cgroup on the kind node
pod_cpu() {
  local uid; uid=$(kubectl -n "$1" get pods -l "$2" -o jsonpath='{.items[0].metadata.uid}')
  docker exec "$NODE" sh -c "f=\$(find /sys/fs/cgroup/kubelet.slice -maxdepth 3 -name '*pod${uid//-/_}.slice' | head -1);
    awk '/^usage_usec/{u=\$2} /^throttled_usec/{t=\$2} END{print u, t}' \$f/cpu.stat"
}
cpu_all() {  # one line: name usage throttled ...
  : "${BRANCH_ID:=$(branch_k8s_id "$BRANCH")}"
  local id=$BRANCH_ID
  echo "postgres $(pod_cpu xata-clusters "cnpg.io/cluster=$id,cnpg.io/podRole=instance")" \
       "pgbouncer $(pod_cpu xata-clusters "cnpg.io/cluster=$id,cnpg.io/podRole=pooler")" \
       "gateway $(pod_cpu xata app=gateway)" "redis $(pod_cpu "$NS" app=redis)"
}
now_s() { python3 -c 'import time; print(time.time())'; }

# session_run <path> <system> <label> <think> <conc> <durability>: one run, with the server pods' CPU
# over the run (cores = CPU seconds / wall seconds) and seconds throttled, then cleanup
session_run() {
  local path=$1 sys=$2 label=$3 think=$4 conc=$5 d=$6 c0 c1 t0 t1 line
  [ "$sys" = postgres ] && py "$path" ctl.py pg-idle-reset
  c0=$(cpu_all); t0=$(now_s)
  line=$(py "$path" agentmem.py run --system "$sys" --label "$label" --think "$think" --concurrency "$conc")
  t1=$(now_s); c1=$(cpu_all)
  python3 - "$line" "$c0" "$c1" "$t0" "$t1" "$d" <<'PY' | record sessions
import json, sys
line, c0, c1, t0, t1, d = sys.argv[1:]
r = json.loads(line)
def parse(s):
    w = s.split()
    return {w[i]: (int(w[i + 1]), int(w[i + 2])) for i in range(0, len(w), 3)}
a, b, wall = parse(c0), parse(c1), float(t1) - float(t0)
r["durability"] = d
r["server_cpu"] = {k: {"cores": round((b[k][0] - a[k][0]) / 1e6 / wall, 2),
                       "throttled_s": round((b[k][1] - a[k][1]) / 1e6, 1)} for k in a}
print(json.dumps(r, sort_keys=True))
PY
  py "$path" agentmem.py cleanup --system "$sys" >/dev/null
}

up() {
  if ! "$XATA" branch describe "${A[@]}" --branch "$BRANCH" --json >/dev/null 2>&1; then
    say "create branch $BRANCH (empty, $XATA_INSTANCE, scale-to-zero off)"
    "$XATA" branch create "${A[@]}" --region "$XATA_REGION" --name "$BRANCH" --no-parent --storage 10 \
      --postgres-version "${PG_VERSION:-postgres:18.6}" --instance-type "$XATA_INSTANCE" --replicas 0 \
      --scale-to-zero false --json >/dev/null 2>"$STATE/$BRANCH.err"
  fi
  wait_ready "$BRANCH"
  local id; id=$(branch_k8s_id "$BRANCH")
  say "gateway at the chart's resources"
  gateway_resources chart
  kubectl apply -f "$HERE/manifests/redis.yaml" >/dev/null
  # Connection strings. Both go through the Xata SQL gateway: they keep the branch hostname for
  # TLS SNI and connect to the gateway Service (from a pod, *.127.0.0.1.nip.io is the pod itself).
  # The gateway sends a plain <branch> hostname straight to Postgres (rw) and <branch>-pooler to the
  # branch's PgBouncer (services/gateway/session/proxy.go, resolver.go parseEndpoint).
  local url gw; url=$(cat "$(branch_url "$BRANCH")")
  gw=$(kubectl -n xata get svc gateway -o jsonpath='{.spec.clusterIP}')
  python3 - "$url" "$gw" "$id" > "$STATE/agentmem-secret.env" <<'EOF'
import sys, urllib.parse as u
url, gw, bid = sys.argv[1:]
p = u.urlsplit(url)
q = dict(u.parse_qsl(p.query))
port = p.port
rw = u.urlunsplit((p.scheme, p.netloc, p.path, u.urlencode({**q, "hostaddr": gw}), ""))
pooled = u.urlunsplit((p.scheme, p.netloc.replace(f"@{bid}.", f"@{bid}-pooler."), p.path,
                       u.urlencode({**q, "hostaddr": gw}), ""))
assert pooled != rw, "branch id not found in the hostname"
print(f"PG_URL_GATEWAY={rw}\nPG_URL_GATEWAY_POOLER={pooled}")
EOF
  chmod 600 "$STATE/agentmem-secret.env"
  kubectl -n "$NS" create secret generic hols-bench-pg --from-env-file="$STATE/agentmem-secret.env" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  say "build and load the bench image"
  docker build -q -t "$IMAGE" "$HERE/bench" >/dev/null
  kind load docker-image "$IMAGE" >/dev/null
  kubectl -n "$NS" delete pod bench --ignore-not-found --wait >/dev/null
  kubectl apply -f "$HERE/manifests/bench.yaml" >/dev/null
  kubectl -n "$NS" wait --for=condition=Ready pod/bench --timeout=300s >/dev/null
  kubectl -n "$NS" rollout status deploy/redis --timeout=300s >/dev/null
  say "versions"
  {
    py gateway ctl.py pg-check
    bench python -c "import importlib.metadata as m, json; print(json.dumps({'kind': 'versions', **{p: m.version(p) for p in ['langgraph','langgraph-checkpoint-postgres','langgraph-checkpoint-redis','langchain-core','psycopg','redis','redisvl']}}))"
    echo "{\"kind\": \"gateway\", \"resources\": $(kubectl -n xata get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].resources}'), \"gomaxprocs\": \"$(kubectl -n xata get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].env[0].value}')\"}"
    echo "{\"kind\": \"platform\", \"xata\": \"$(git -C "${XATA_SRC:-$STATE/xata}" rev-parse --short HEAD)\", \"cli\": \"$("$XATA" version --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["CLIVersion"])')\", \"docker_mem_mib\": $(( $(docker info --format '{{.MemTotal}}') / 1024 / 1024 )), \"machine\": \"$(sysctl -n machdep.cpu.brand_string 2>/dev/null || uname -m)\"}"
  } | record up
}

durability() {  # durability <strict|relaxed>: the pair is set on both systems at once
  if [ "$1" = strict ]; then py gateway ctl.py pg-sync on; py gateway ctl.py redis-durability always
  else py gateway ctl.py pg-sync off; py gateway ctl.py redis-durability everysec; fi
}

setup() {
  py gateway agentmem.py setup --system postgres | record setup
  py gateway agentmem.py setup --system redis | record setup
  py gateway agentmem.py recall --system postgres | record setup
  py gateway agentmem.py recall --system redis | record setup
  durability strict | record setup
  py gateway agentmem.py footprint --system postgres --label gateway | record setup
  py gateway agentmem.py footprint --system redis | record setup
}

sessions() {
  local d think conc
  for d in strict relaxed; do
    durability "$d" | record sessions
    for think in 0 real; do
      for conc in 1 10 50; do
        say "sessions: $d think=$think C=$conc"
        session_run gateway postgres gateway "$think" "$conc" "$d"
        session_run gateway redis redis "$think" "$conc" "$d"
        if [ "$think" = 0 ]; then
          session_run gateway_pooler postgres gateway-pooler 0 "$conc" "$d"
        fi
      done
    done
  done
}

kill_server() {  # kill_server <system>: SIGKILL the server process, the way a crash would
  if [ "$1" = redis ]; then
    docker exec "$NODE" pkill -9 -x redis-server
  else
    kubectl -n xata-clusters exec "$(branch_k8s_id "$BRANCH")-1" -c postgres -- \
      sh -c 'kill -9 "$(head -1 "$PGDATA/postmaster.pid")"'
  fi
}

kill_tests() {
  local d sys out
  for d in strict relaxed; do
    durability "$d" | record kill
    for sys in postgres redis; do
      say "kill: $sys $d"
      out=$(mktemp)
      py gateway agentmem.py killwrite --system "$sys" --concurrency 10 > "$out" &
      local pid=$!
      until grep -q WRITING "$out" 2>/dev/null; do sleep 1; done
      sleep 15
      echo "{\"kind\": \"kill_sent\", \"system\": \"$sys\", \"utc\": \"$(date -u +%H:%M:%S)\"}" | record kill
      kill_server "$sys"
      wait "$pid" || true
      grep -v WRITING "$out" | sed "s/^{/{\"durability\": \"$d\", /" | record kill
      rm -f "$out"
      [ "$sys" = redis ] && kubectl -n "$NS" rollout status deploy/redis --timeout=300s >/dev/null
      wait_ready "$BRANCH"
    done
  done
}

expiry() {
  durability strict | record expiry
  local n v
  for n in 10000 100000; do
    for v in redis_ttl pg_delete pg_partition; do
      say "expiry: $v n=$n"
      py gateway expiry.py --variant "$v" --n "$n" | record expiry
    done
  done
}

hotstate() {
  durability strict | record hotstate
  local conc v
  for v in redis_list pg_jsonb pg_rows; do
    py gateway_pooler hotstate.py --variant "$v" --probe | record hotstate
    for conc in 10 50; do
      say "hotstate: $v C=$conc"
      py gateway_pooler hotstate.py --variant "$v" --concurrency "$conc" | record hotstate
    done
  done
}

memsize() {  # the preloaded memories on a compacted store: Postgres after VACUUM FULL, Redis after a restart from its AOF
  py gateway memsize.py prune --system postgres
  py gateway memsize.py prune --system redis
  py gateway memsize.py size --system postgres | record memsize
  py gateway ctl.py redis-durability always >/dev/null
  bench python -c "import asyncio, common as c; r = c.redis_client(); asyncio.run(c.redis_compact_aof(r))"
  kubectl -n "$NS" rollout restart deploy/redis >/dev/null
  kubectl -n "$NS" rollout status deploy/redis --timeout=300s >/dev/null
  until bench python -c "import asyncio, common as c; r = c.redis_client(); asyncio.run(r.ping())" 2>/dev/null; do sleep 2; done
  py gateway memsize.py size --system redis | record memsize
}

down() {
  kubectl delete namespace "$NS" --ignore-not-found >/dev/null
  "$XATA" branch delete "${A[@]}" --branch "$BRANCH" --yes >/dev/null 2>&1 || true
  rm -f "$STATE/agentmem-secret.env"
  gateway_resources local
}

case "${1:-all}" in
  up) up ;;
  setup) setup ;;
  memsize) memsize ;;
  sessions) sessions ;;
  kill) kill_tests ;;
  expiry) expiry ;;
  hotstate) hotstate ;;
  down) down ;;
  one) shift; session_run "$@" ;;  # one <gateway|gateway_pooler> <postgres|redis> <label> <0|real> <C> <durability label>
  all) up; setup; sessions; kill_tests; expiry; hotstate; memsize ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
