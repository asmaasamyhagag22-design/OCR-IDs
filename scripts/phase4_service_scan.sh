#!/bin/bash
# Phase 4 OCR service: the image-contents scan, with its inputs made the one declared way.
#   wsl -d Ubuntu -e bash scripts/phase4_service_scan.sh [IMAGE]
# The base tar is the export of the base image pinned by digest in the Dockerfile,
# never of the service image (the scanner's precondition, found by the
# instrument-validator's fourth pass): a base tar holding any app/ path is refused.
set -euo pipefail
ROOT=/mnt/c/Users/asmaa/Documents/OCR_ID_parallel
WORK="$HOME/build/scan"
cd "$ROOT"
IMAGE="${1:-$(bash scripts/phase4_service_docker.sh image)}"
BASE="$(sed -n 's/^FROM \(python:3\.11\.9-slim@sha256:[0-9a-f]\{64\}\)$/\1/p' Dockerfile)"
[ -n "$BASE" ] || { echo "PHASE4_SERVICE_BLOCKED:no pinned base in the Dockerfile"; exit 1; }
rm -rf "$WORK"
mkdir -p "$WORK"
docker save "$IMAGE" -o "$WORK/image.tar"
cid="$(docker create "$IMAGE")"; docker export "$cid" > "$WORK/rootfs.tar"; docker rm "$cid" >/dev/null
cid="$(docker create "$BASE")"; docker export "$cid" > "$WORK/base.tar"; docker rm "$cid" >/dev/null
if tar -tf "$WORK/base.tar" | grep -q '^\(\./\)\?app/'; then
  echo "PHASE4_SERVICE_BLOCKED:the base tar holds an app/ path: it is not the pinned base image"; exit 1
fi
echo "image=$IMAGE base=$BASE"
set +e
"$HOME/venvs/ocrid-service-py3119/bin/python" scripts/phase4_service_image_scan.py \
  --rootfs "$WORK/rootfs.tar" --save "$WORK/image.tar" --base "$WORK/base.tar" \
  --anchor-site-packages "$HOME/venvs/ocrid-service-py3119/lib/python3.11/site-packages" \
  --model-manifest docker/model_files.sha256 --out "$ROOT/artifacts/phase4_service/image_scan" \
  --image-ref "$IMAGE" --image-id "$(docker image inspect --format '{{.Id}}' "$IMAGE")" --base-ref "$BASE"
status=$?
rm -f "$WORK/image.tar" "$WORK/rootfs.tar" "$WORK/base.tar"     # 4.7 GB of page cache otherwise
exit $status
