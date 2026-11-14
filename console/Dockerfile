# The Fixed14 review console. Build from the repository root:
#   docker compose build console
# It holds the console's code and three small read-only inputs, and nothing else:
# no model, no data, no secret. Settings come from CONSOLE_* variables at run time.

FROM python:3.11.9-slim@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# a font with Arabic-Indic digits, used only to draw the demonstration's synthetic cards
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY console/requirements.txt /tmp/console-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --timeout 120 --retries 20 pip==26.2.1 \
 && pip install --timeout 120 --retries 20 -r /tmp/console-requirements.txt \
 && rm /tmp/console-requirements.txt \
 && pip uninstall -y -q setuptools wheel  # build tools from the base image, unused at run time (pip-audit)

WORKDIR /app
# governorate names, the sealed routing threshold, and the API client's connection constants
COPY config/governorates_v1.json /app/config/governorates_v1.json
COPY configs/phase2m_h_frozen_candidate.json /app/configs/phase2m_h_frozen_candidate.json
COPY scripts/natid_ocr_pipeline_model_API_calling.py /app/scripts/natid_ocr_pipeline_model_API_calling.py
COPY console/ /app/console/

RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin console
USER console

EXPOSE 8090
# a real GET /login over HTTP or HTTPS (console/healthcheck.py), so a wedged application is restarted
HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=3 \
  CMD ["python", "-m", "console.healthcheck"]
CMD ["python", "-m", "console", "serve"]
