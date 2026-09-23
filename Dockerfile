# EnergyOptimizer als Container – läuft auf dem Raspberry Pi (arm64) und auf x86.
FROM python:3.12-slim

ARG EO_BUILD=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EO_DATA_DIR=/data \
    EO_PORT=8080 \
    TZ=Europe/Zurich \
    EO_BUILD=${EO_BUILD}

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/energyoptimizer ./energyoptimizer

VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/favicon.ico' % os.environ.get('EO_PORT','8080'), timeout=4)"

CMD ["python", "-m", "energyoptimizer"]
