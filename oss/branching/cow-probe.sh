#!/usr/bin/env bash
# Can this Docker host run a copy-on-write snapshot driver, and what would CoW save?
# Needs only Docker (no cluster). In one privileged container on the Docker VM kernel it:
#   1. lists the device-mapper targets and tries to load dm-thin-pool / dm-snapshot
#      (LVM-based CSI drivers such as TopoLVM need them for snapshots);
#   2. on a btrfs and an XFS (reflink=1) loop filesystem, writes SIZE_MB files of 1 MiB
#      (random, so gzip cannot shrink them) and times the hostpath driver's way —
#      tar czf + tar zxf — against cp --reflink=always.
# Prints plain text lines; nothing is left behind.
#
#   cow-probe.sh [SIZE_MB=1400]
set -euo pipefail
SIZE_MB=${1:-1400}
IMAGE=alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc

echo "date $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "docker $(docker info --format '{{.OperatingSystem}} · kernel {{.KernelVersion}} · {{.NCPU}} CPU · {{.MemTotal}} B')"
docker run --rm --privileged -e SIZE_MB="$SIZE_MB" -v /lib/modules:/lib/modules:ro "$IMAGE" sh -c '
apk add -q kmod device-mapper btrfs-progs xfsprogs coreutils tar gzip util-linux >/dev/null 2>&1
echo "dm targets: $(dmsetup targets | awk "{print \$1}" | tr "\n" " ")"
for m in dm_thin_pool dm_snapshot; do modprobe $m 2>/dev/null && echo "modprobe $m: loaded" || echo "modprobe $m: not available"; done
el() { awk "BEGIN{printf \"%.2f\", $2-$1}"; }
for fs in btrfs xfs; do
  truncate -s $((SIZE_MB * 3 + 1024))M /img.$fs; L=$(losetup -f --show /img.$fs)
  if [ $fs = btrfs ]; then mkfs.btrfs -q $L; else mkfs.xfs -q -m reflink=1 $L; fi
  mkdir -p /m$fs && mount $L /m$fs && mkdir /m$fs/src
  i=0; while [ $i -lt $SIZE_MB ]; do head -c 1048576 /dev/urandom > /m$fs/src/f$i; i=$((i+1)); done; sync
  t0=$(date +%s.%N); tar czf /m$fs/snap.tgz -C /m$fs/src . && mkdir /m$fs/rest && tar zxf /m$fs/snap.tgz -C /m$fs/rest; sync; t1=$(date +%s.%N)
  cp -a --reflink=always /m$fs/src /m$fs/clone; sync; t2=$(date +%s.%N)
  echo "$fs ${SIZE_MB} MiB in ${SIZE_MB} files: tar czf+zxf $(el $t0 $t1) s · cp --reflink $(el $t1 $t2) s"
  umount /m$fs; losetup -d $L; rm /img.$fs
done'
