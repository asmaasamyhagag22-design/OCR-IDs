# The Fixed14 OCR verification service and competition CLI (Phase 4 OCR service).
# Build with scripts/phase4_service_docker.sh build: it stages the context from
# `git archive HEAD` plus the model files (symlinks and junctions resolved), and
# tags the image fixed14-ocr-service:fixed14-bcfinal-3101-<git sha>.
# Layers go from least to most frequently changed. The image holds no data and
# no secret: the API key comes from the OCR_API_KEY environment variable at run time.

# 1. the base, pinned by digest
FROM python:3.11.9-slim@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 2. system libraries, versions pinned: OpenMP for Paddle, and the two X session libraries the owner named
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1=12.2.0-14+deb12u1 libsm6=2:1.2.3-1 libice6=2:1.0.10-1 \
 && rm -rf /var/lib/apt/lists/*

# 3. Python requirements: exact pins, no resolution; then the headless OpenCV binary (D-046).
#    pip's download cache is a BuildKit cache mount: a download interrupted on a slow link
#    resumes on the next build, and the cache never enters the image (D-050).
COPY requirements-linux.txt requirements-linux-opencv-headless.txt /tmp/requirements/
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --no-deps --only-binary=:all: --timeout 120 --retries 20 -r /tmp/requirements/requirements-linux.txt \
 && pip install --no-deps --only-binary=:all: --force-reinstall --timeout 120 --retries 20 \
      -r /tmp/requirements/requirements-linux-opencv-headless.txt \
 && python -c "import hashlib, pathlib, cv2; so = pathlib.Path(cv2.__file__).parent / 'cv2.abi3.so'; digest = hashlib.sha256(so.read_bytes()).hexdigest(); assert digest == '72c7fe9b389ecd9549b6e9cdca35ee11940de15d13d46722b8412865cafabc78', digest" \
 && rm -rf /tmp/requirements

# 4. model weights and model configs, each file's sha256 verified inside the image
WORKDIR /app
COPY docker/model_files.sha256 /app/docker/model_files.sha256
COPY artifacts/ /app/artifacts/
COPY third_party/ /app/third_party/
COPY configs/ /app/configs/
COPY config/ /app/config/
RUN sha256sum --strict --check /app/docker/model_files.sha256 \
 && test "$(find /app/artifacts /app/third_party /app/configs /app/config -type f | wc -l)" = "$(grep -c . /app/docker/model_files.sha256)"

# 5. application code
COPY scripts/phase2l_a.py scripts/phase2l_b.py /app/scripts/
COPY src/ /app/src/

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="fixed14-ocr-service" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.description="National ID OCR verification service and competition CLI (BC-final seed 3101)"

RUN useradd --system --uid 10001 --user-group --create-home --home-dir /home/ocr ocr
USER 10001:10001
# OpenMP threads sleep instead of spinning, and the service caps its threads at 4 (D-053):
# spinning threads made inferences several times slower when the host was busy
ENV HOME=/home/ocr \
    OCR_PORT=8081 \
    OCR_HOST=0.0.0.0 \
    OMP_WAIT_POLICY=PASSIVE \
    OCR_CPU_THREADS=4 \
    OCR_PADDLE_CACHE_DIR=/tmp/ocr_paddlex_cache \
    DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    TF_CPP_MIN_LOG_LEVEL=3
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=10s --start-period=240s --retries=3 \
  CMD ["python", "-m", "src.service.healthcheck"]
CMD ["python", "-m", "src.service"]
