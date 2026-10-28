#!/usr/bin/env bash
# Bring up Xata OSS on a local kind cluster, the way upstream's README does (kind + tilt),
# plus the three local adjustments this repository needs, then log the CLI in and create
# the `hols` project with a `main` branch and a seeded `base` branch.
#
#   up.sh            # idempotent: reuses the cluster, the checkout and the project
#
# Needs: Docker with at least 16 GiB of memory, kind, tilt, helm, kubectl, psql, the xata CLI.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/env.sh"

XATA_REF=${XATA_REF:-f197e7bad539a81b424018765ee4e8799ed184f0}   # xataio/xata main, 2026-10-23
SRC=${XATA_SRC:-$STATE/xata}
PG_VERSION=${PG_VERSION:-postgres:18.6}

for t in docker kind tilt helm kubectl psql "$XATA"; do
  command -v "$t" >/dev/null || { echo "missing: $t (see README, Prerequisites)" >&2; exit 1; }
done
mem=$(docker info --format '{{.MemTotal}}')
[ "$mem" -ge 15000000000 ] || { echo "Docker has $((mem / 1024 / 1024)) MiB; give it 16 GiB (README, Prerequisites)" >&2; exit 1; }

# 1. Source at the pinned commit -------------------------------------------------
[ -d "$SRC/.git" ] || git clone -q https://github.com/xataio/xata.git "$SRC"
git -C "$SRC" fetch -q origin "$XATA_REF" 2>/dev/null || git -C "$SRC" fetch -q origin
git -C "$SRC" checkout -q "$XATA_REF"

# 2. Cluster and tilt --------------------------------------------------------------
kind get clusters 2>/dev/null | grep -qx kind || kind create cluster --wait 10m
kubectl config use-context kind-kind >/dev/null
if ! tilt get session >/dev/null 2>&1; then
  (cd "$SRC" && nohup tilt up --stream > "$STATE/tilt.log" 2>&1 &)
  echo "tilt up started; log: $STATE/tilt.log"
fi

wait_tilt() {
  local start bad out
  start=$(date +%s)
  while true; do
    out=$(tilt get uiresources -o jsonpath='{range .items[*]}{.metadata.name}={.status.runtimeStatus}/{.status.updateStatus}{"\n"}{end}' 2>/dev/null || true)
    bad=$(echo "$out" | grep -v '^e2e-tests=' | grep -vE '=(ok|not_applicable)/(ok|not_applicable)$' || true)
    if [ -n "$out" ] && [ -z "$bad" ]; then echo "tilt: all resources ready after $(( $(date +%s) - start ))s"; return 0; fi
    if echo "$bad" | grep -q error; then echo "tilt: resources in error:" >&2; echo "$bad" >&2; return 1; fi
    [ $(( $(date +%s) - start )) -gt 3600 ] && { echo "tilt: not ready after 60 min:" >&2; echo "$bad" >&2; return 1; }
    sleep 10
  done
}
wait_tilt

# 3. Local adjustments (README, Local deviations) ------------------------------------
# a. One Keycloak replica instead of two: frees 2 GiB of requests; the labs log in with an API key.
kubectl -n xata patch keycloaks.k8s.keycloak.org auth-keycloak --type merge -p '{"spec":{"instances":1}}' >/dev/null
# b. Branch egress to the API server and RustFS (manifests/allow-branch-egress.yaml.tmpl).
API_IP=$(kubectl get endpointslice -n default -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].endpoints[0].addresses[0]}')
API_PORT=$(kubectl get endpointslice -n default -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].ports[0].port}')
API_IP=$API_IP API_PORT=$API_PORT envsubst '${API_IP} ${API_PORT}' < "$HERE/manifests/allow-branch-egress.yaml.tmpl" | kubectl apply -f - >/dev/null

# 4. CLI login with a dev API key ---------------------------------------------------
if ! "$XATA" organization list --profile local --json >/dev/null 2>&1; then
  key=$(kubectl -n xata exec deploy/auth -c auth -- /server create_dev_api_key 2>/dev/null | tail -1)
  "$XATA" auth login --force --profile local --api-key "$key" \
    --issuer http://localhost:8080/realms/xata --api-url http://localhost:5081 >/dev/null
  unset key
fi
"$XATA" auth switch local >/dev/null

# 5. Project, main and base ---------------------------------------------------------
if ! PROJECT=$(project_id hols 2>/dev/null); then
  "$XATA" project create --organization "$XATA_ORG" --name hols --branch-name main --region "$XATA_REGION" \
    --replicas 0 --instance-type "$XATA_INSTANCE" --storage 10 --postgres-version "$PG_VERSION" \
    --scale-to-zero-base false --scale-to-zero-child true --inactivity-period-child 5 --json >/dev/null
  PROJECT=$(project_id hols)
fi
A=(--organization "$XATA_ORG" --project "$PROJECT")
wait_ready main
if ! "$XATA" branch describe "${A[@]}" --branch base --json >/dev/null 2>&1; then
  "$XATA" branch create "${A[@]}" --region "$XATA_REGION" --name base --no-parent --storage 10 \
    --postgres-version "$PG_VERSION" --instance-type "$XATA_INSTANCE" --replicas 0 --scale-to-zero false --json >/dev/null 2>&1
  wait_ready base
  psql "$(cat "$(branch_url base)")" -X -q -v ON_ERROR_STOP=1 -f "$HERE/seed-app.sql"
fi
echo "ready: project hols ($PROJECT), branches main and base"
q base "select version()"
