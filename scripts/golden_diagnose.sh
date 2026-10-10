#!/bin/bash
# Why is the service container unhealthy? Runs the same startup golden self-test as the service, in a
# throwaway container with no network, and prints which checks failed. The service log records only the
# error type (models_failed, error=GoldenMismatch); this prints the check names and the CPU's vector
# instruction sets. The golden inputs are synthetic: nothing printed is an image or an ID.
#   bash scripts/golden_diagnose.sh [IMAGE]          (default: the image for this checkout's HEAD)
# Known cause (D-100): on a CPU without AVX-512 the margins differ from the stored ones by up to about
# 1e-5 (band) and 2e-3 (end to end) while every reading stays the same, which exceeds the self-test's
# tolerances, so the service refuses to become ready. ONEDNN_MAX_CPU_ISA=AVX2 reproduces it on an
# AVX-512 machine.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${1:-$(bash scripts/phase4_service_docker.sh image)}"
echo "cpu: $(sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo | head -1)"
echo "cpu vector sets: $(grep -o -w -E 'avx512f|avx2|avx|sse4_1' /proc/cpuinfo | sort -u | tr '\n' ' ')"
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 \
  ${ONEDNN_MAX_CPU_ISA:+-e ONEDNN_MAX_CPU_ISA="$ONEDNN_MAX_CPU_ISA"} --entrypoint python "$IMAGE" -c '
import sys
from src.service import golden
from src.service.recognizer import Recognizer
recognizer = Recognizer()
recognizer.load()
try:
    print("GOLDEN_OK", golden.run(recognizer.runtime))
except golden.GoldenMismatch as exc:
    print("GOLDEN_FAIL", exc)
    sys.exit(1)
' 2>/dev/null | grep -E '^GOLDEN_'
