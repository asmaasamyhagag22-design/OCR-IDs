#!/bin/bash
# Phase 4 OCR service: build, run and exercise the Docker image, from WSL (Ubuntu).
#   wsl -d Ubuntu -e bash scripts/phase4_service_docker.sh <command> [args]
#
#   context          stage the build context: `git archive HEAD` (code, configs, Dockerfile,
#                    lock, model-file manifest) plus the model files copied with every
#                    symlink and junction resolved; verify every hash; refuse any symlink
#   build            context, then docker build; tag fixed14-ocr-service:fixed14-bcfinal-3101-<sha>
#   serve            run the service detached on 127.0.0.1:8010 (read-only root, no capabilities)
#   wait-healthy     wait until the container's HEALTHCHECK (/ready) is healthy
#   stop             stop and remove the service container
#   cli N [CYCLE]    run the CLI in a fresh container on the staged images, N workers,
#                    no network; CYCLE repeats the list to that many images (timing)
#   image            print the image reference for HEAD
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTX="$HOME/build/fixed14-ocr-context"
OUT="$ROOT/artifacts/phase4_service"
# the key lives on WSL's own filesystem, mode 600, never under /mnt/c (NTFS ignores the mode)
SECRETS="$HOME/build/secrets"
NAME=fixed14-ocr
# The worktree's .git points at a Windows path, which WSL's git cannot follow; the
# Windows git is called through WSL interop instead (same repository, same HEAD).
cd "$ROOT"
git() { git.exe "$@" | tr -d $'\r'; }
git_raw() { git.exe "$@"; }
SHA="$(git rev-parse --short HEAD)"
FULL_SHA="$(git rev-parse HEAD)"
IMAGE="fixed14-ocr-service:fixed14-bcfinal-3101-$SHA"

ensure_key() {
  mkdir -p "$SECRETS"
  chmod 700 "$SECRETS"
  umask 077
  if [ ! -s "$SECRETS/api_key.txt" ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$SECRETS/api_key.txt"
  fi
  printf 'OCR_API_KEY=%s\n' "$(cat "$SECRETS/api_key.txt")" > "$SECRETS/ocr.env"
  chmod 600 "$SECRETS/api_key.txt" "$SECRETS/ocr.env"
}

cmd_context() {
  rm -rf "$CTX"
  mkdir -p "$CTX"
  # the code is exactly docker/app_code_files.txt: the import closure of the image's entry points
  # (scripts/phase4_final_scope.py, phase 4 final Stage 7); research files in src/ never enter the context
  mapfile -t tracked < <(printf '%s\n' Dockerfile .dockerignore requirements-linux.txt \
    requirements-linux-opencv-headless.txt docker/model_files.sha256 docker/app_code_files.txt;
    tr -d '\r' < docker/app_code_files.txt;
    sed -n 's#^[0-9a-f]\{64\}  /app/\(configs/.*\|config/.*\)$#\1#p' docker/model_files.sha256)
  git_raw archive --format=tar HEAD -- "${tracked[@]}" | tar -x -C "$CTX"
  rm -f "$CTX/src/extraction/second_model.py"
  sed -n 's#^[0-9a-f]\{64\}  /app/\(artifacts/.*\|third_party/.*\)$#\1#p' docker/model_files.sha256 |
  while read -r rel; do
    mkdir -p "$CTX/$(dirname "$rel")"
    cp -L "$ROOT/$rel" "$CTX/$rel"
  done
  if [ -n "$(find "$CTX" -type l)" ]; then echo "PHASE4_SERVICE_BLOCKED:symlink_in_context"; exit 1; fi
  (cd "$CTX" && sed 's#  /app/#  #' docker/model_files.sha256 | sha256sum --strict --quiet --check)
  echo "context: $(find "$CTX" -type f | wc -l) files, $(du -sh "$CTX" | cut -f1), every model-file hash verified, no symlink"
}

cmd_build() {
  if [ -n "$(git status --porcelain -- Dockerfile .dockerignore requirements-linux.txt requirements-linux-opencv-headless.txt docker src scripts/phase2l_a.py scripts/phase2l_b.py configs config)" ]; then
    echo "PHASE4_SERVICE_BLOCKED:image_inputs_differ_from_HEAD (commit first: the image is built from HEAD)"; exit 1
  fi
  cmd_context
  docker build --build-arg GIT_SHA="$FULL_SHA" -t "$IMAGE" "$CTX"
  docker image inspect --format '{{.Id}} {{.Size}}' "$IMAGE"
  echo "$IMAGE"
}

cmd_serve() {
  ensure_key
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -p 127.0.0.1:8010:8010 --env-file "$SECRETS/ocr.env" \
    --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 \
    --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 \
    --memory 3g --pids-limit 256 "$IMAGE"
}

cmd_wait_healthy() {
  for _ in $(seq 1 120); do
    state="$(docker inspect --format '{{.State.Health.Status}}' "$NAME")"
    if [ "$state" = "healthy" ]; then echo healthy; return 0; fi
    if [ "$state" = "unhealthy" ]; then echo unhealthy; return 1; fi
    sleep 5
  done
  echo timeout; return 1
}

cmd_stop() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }

cmd_cli() {
  workers="$1"; cycle="${2:-}"
  mkdir -p "$OUT/cli_container"
  tag="w${workers}${cycle:+_c$cycle}"
  extra=()
  if [ -n "$cycle" ]; then extra=(--cycle-to "$cycle"); fi
  started="$(date +%s.%N)"
  docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 \
    --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --pids-limit 512 \
    -v "$OUT/stage/dev_ctl:/input/dev_ctl:ro" -v "$OUT/stage/orig:/input/orig:ro" \
    -v "$OUT/cli_container:/output" "$IMAGE" \
    sh -c "python -m src.service.cli /input/dev_ctl --output /output/dev_ctl_$tag.csv --workers $workers ${extra[*]} 2>/output/dev_ctl_$tag.log;
           status=\$?; echo \"memory_peak_bytes \$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)\" >> /output/dev_ctl_$tag.log;
           if [ -z \"$cycle\" ]; then python -m src.service.cli /input/orig --output /output/orig_$tag.csv --workers $workers 2>/output/orig_$tag.log; fi; exit \$status"
  ended="$(date +%s.%N)"
  echo "wall_seconds $(python3 -c "print(round($ended - $started, 1))")" | tee -a "$OUT/cli_container/dev_ctl_$tag.log"
}

case "${1:-}" in
  context) cmd_context ;;
  build) cmd_build ;;
  serve) cmd_serve ;;
  wait-healthy) cmd_wait_healthy ;;
  stop) cmd_stop ;;
  cli) shift; cmd_cli "$@" ;;
  image) echo "$IMAGE" ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
