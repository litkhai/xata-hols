#!/usr/bin/env bash
# Stop tilt and delete the kind cluster. Keeps the source checkout and the CLI profile.
#   down.sh            # cluster only
#   down.sh --all      # also the state directory (connection strings, checkout, logs)
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/env.sh"
pkill -f 'tilt up --stream' 2>/dev/null && echo "tilt stopped"
kind delete cluster
docker rm -f xata-hols-source >/dev/null 2>&1 && echo "source container removed"
[ "${1:-}" = --all ] && rm -rf "$STATE" && echo "removed $STATE"
exit 0
