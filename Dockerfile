FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLEXTRA_CONFIG_DIR=/config \
    PLEXTRA_PORT=9898

RUN groupadd --gid 1000 plextra \
 && useradd --uid 1000 --gid 1000 --home-dir /app --shell /usr/sbin/nologin plextra

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY plextra ./plextra

RUN mkdir -p /config && chown -R plextra:plextra /config /app

VOLUME ["/config"]
EXPOSE 9898
USER plextra

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys;p=os.environ.get('PLEXTRA_PORT','9898');sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "plextra"]
