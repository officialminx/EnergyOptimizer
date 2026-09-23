# EnergyOptimizer: solar surplus control for Shelly plugs, for Raspberry Pi (arm64) and x86.
FROM python:3.12-slim

ARG EO_BUILD=dev

LABEL org.opencontainers.image.title="EnergyOptimizer" \
      org.opencontainers.image.description="Switches Shelly plugs and MQTT loads with Solar-Log PV surplus" \
      org.opencontainers.image.source="https://github.com/officialminx/energyoptimizer" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EO_DATA_DIR=/data \
    EO_PORT=80 \
    TZ=Europe/Zurich \
    EO_BUILD=${EO_BUILD}

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/energyoptimizer ./energyoptimizer

VOLUME /data
EXPOSE 80

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/favicon.ico' % os.environ.get('EO_PORT','80'), timeout=4)"

CMD ["python", "-m", "energyoptimizer"]
