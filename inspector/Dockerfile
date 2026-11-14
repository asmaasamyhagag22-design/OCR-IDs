# The model inspector: the OCR service image, unchanged, plus the inspector package.
# Build from the repository root (docker compose does this):
#   docker compose build inspector        (OCR_IMAGE names the base, see compose.yaml)
# The base image's code, weights, configs and hash checks are inherited as they are;
# this layer adds only inspector/ and a different start command.
ARG OCR_IMAGE
FROM ${OCR_IMAGE}

COPY inspector/ /app/inspector/

ENV INSPECTOR_HOST=0.0.0.0 \
    INSPECTOR_PORT=8082
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=10s --start-period=240s --retries=3 \
  CMD ["python", "-c", "import json, os, urllib.request; r = urllib.request.Request('http://127.0.0.1:8082/health', headers={'X-Inspector-Key': os.environ.get('INSPECTOR_KEY', '')}); raise SystemExit(0 if json.load(urllib.request.urlopen(r, timeout=8)).get('ready') else 1)"]
CMD ["python", "-m", "inspector"]
