#!/usr/bin/env bash
# POST one SQL statement to the gateway's HTTP endpoint (openapi/gateway.yaml, `/sql`).
# The branch connection string goes in the Connection-String header, read from a
# mode-600 file so it never appears on the command line.
#
#   query.sh <branch> '<sql>' [extra curl args…]
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../platform/env.sh"
PROJECT=${PROJECT:-$(project_id hols)}
branch=${1:?branch}; sql=${2:?sql}; shift 2

url=$(cat "$(branch_url "$branch")")
host=$(python3 -c 'import sys,urllib.parse as u; print(u.urlsplit(sys.argv[1]).hostname)' "$url")
hdr="$STATE/$branch.http-headers"
(umask 077; printf 'Connection-String: %s\n' "$url" > "$hdr")

# The local gateway's HTTPS port is tilt's port-forward (8443) with a self-signed certificate.
curl -sk "https://$host:${XATA_GATEWAY_HTTPS_PORT:-8443}/sql" -H @"$hdr" -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}))' "$sql")" "$@"
echo
