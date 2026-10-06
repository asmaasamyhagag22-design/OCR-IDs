#!/bin/bash
# Phase 4 final pass, Stage 5: verify only the changed layers of the rebuilt image (rule `stage_5`).
#   wsl -d Ubuntu -e bash scripts/phase4_final_image.sh layer-scan IMAGE
# Layers 1-15 (base, system libraries, pip, the model files and their check) must be identical
# to ce10649's; the changed layers are scanned by the validated scripts/phase4_final_layer_scan.py.
set -euo pipefail
ROOT=/mnt/c/Users/asmaa/Documents/OCR_ID_parallel
WORK="$HOME/build/final"
REFERENCE=fixed14-ocr-service:fixed14-bcfinal-3101-ce10649
cd "$ROOT"
git() { git.exe "$@"; }
cmd="$1"; IMAGE="$2"
case "$cmd" in
  layer-scan)
    BASE="$(sed -n 's/^FROM \(python:3\.11\.9-slim@sha256:[0-9a-f]\{64\}\)$/\1/p' Dockerfile)"
    rm -rf "$WORK/head" "$WORK/image.tar" "$WORK/base.tar"
    mkdir -p "$WORK/head"
    docker image inspect --format '{{json .RootFS.Layers}}' "$REFERENCE" > "$WORK/ref_layers.json"
    git archive --format=tar HEAD -- src scripts/phase2l_a.py scripts/phase2l_b.py | tar -x -C "$WORK/head"
    rm -f "$WORK/head/src/extraction/second_model.py"
    docker save "$IMAGE" -o "$WORK/image.tar"
    cid="$(docker create "$BASE")"; docker export "$cid" > "$WORK/base.tar"; docker rm "$cid" >/dev/null
    set +e
    "$HOME/venvs/ocrid-service-py3119/bin/python" scripts/phase4_final_layer_scan.py --save "$WORK/image.tar" \
      --reference-diff-ids "$WORK/ref_layers.json" --base-save "$WORK/base.tar" --head-tree "$WORK/head" \
      --min-identical 15 --out "$ROOT/artifacts/phase4_final/layer_scan"
    status=$?
    rm -f "$WORK/image.tar" "$WORK/base.tar"
    echo "image=$IMAGE id=$(docker image inspect --format '{{.Id}}' "$IMAGE") reference=$REFERENCE base=$BASE"
    exit $status ;;
  measure-setup)
    # Stage 6 (amendment 5): the hardened flags except a writable root; the S_dev and S_ctl images and the
    # timing script copied into the container's own filesystem, never mounted
    SPEED="$ROOT/artifacts/phase4_final/speed"
    docker rm -f fixed14-measure >/dev/null 2>&1 || true
    docker create --name fixed14-measure --network none --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 \
      --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 5g --pids-limit 512 \
      "$IMAGE" sleep infinity >/dev/null
    docker cp "$ROOT/artifacts/phase4_service/stage/dev_ctl" fixed14-measure:/work
    docker cp "$ROOT/scripts/phase4_final_speed.py" fixed14-measure:/work/phase4_final_speed.py
    docker cp "$SPEED/order_dev.txt" fixed14-measure:/work/order_dev.txt
    docker cp "$SPEED/order_ctl.txt" fixed14-measure:/work/order_ctl.txt
    docker start fixed14-measure >/dev/null
    # root owns the copies (no chown under --cap-drop ALL); the owner may chmod without a capability
    docker exec -u 0 fixed14-measure sh -c "mkdir -p /work/out /work/dev_only && chmod 777 /work/out /work/dev_only &&       while read f; do ln /work/\$f /work/dev_only/\$f; done < /work/order_dev.txt"
    docker exec fixed14-measure sh -c 'echo images_in_container_dev $(ls /work/dev_only | wc -l)' ;;
  measure)
    # measure IMAGE NAME [PYTHONPATH]: one worker, sequential, S_dev then S_ctl
    name="$3"; pypath="${4:-}"
    SPEED="$ROOT/artifacts/phase4_final/speed"
    for kind in dev ctl; do
      docker exec -e PYTHONPATH="$pypath" fixed14-measure python /work/phase4_final_speed.py --images /work \
        --order "/work/order_$kind.txt" --out "/work/out/${name}_$kind.jsonl"
      docker cp "fixed14-measure:/work/out/${name}_$kind.jsonl" "$SPEED/${name}_$kind.jsonl"
    done ;;
  measure-cli)
    # measure-cli IMAGE WORKERS: the competition CLI on the in-container S_dev copy; wall time with loading
    workers="$3"; SPEED="$ROOT/artifacts/phase4_final/speed"
    started="$(date +%s.%N)"
    docker exec fixed14-measure python -m src.service.cli /work/dev_only --output /work/out/cli_w$workers.csv \
      --workers "$workers" 2>/dev/null
    ended="$(date +%s.%N)"
    docker cp "fixed14-measure:/work/out/cli_w$workers.csv" "$SPEED/cli_w$workers.csv"
    echo "wall_seconds $(python3 -c "print(round($ended - $started, 1))") workers $workers" | tee "$SPEED/cli_w$workers.wall" ;;
  measure-stop) docker rm -f fixed14-measure ;;
  *) echo "usage: layer-scan IMAGE | measure-setup IMAGE | measure IMAGE NAME [PYTHONPATH] | measure-cli IMAGE WORKERS"; exit 2 ;;
esac
