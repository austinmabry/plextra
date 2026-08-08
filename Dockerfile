FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SIDECARR_CONFIG_DIR=/config \
    SIDECARR_PORT=9898

RUN groupadd --gid 1000 sidecarr \
 && useradd --uid 1000 --gid 1000 --home-dir /app --shell /usr/sbin/nologin sidecarr

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY sidecarr ./sidecarr

RUN mkdir -p /config && chown -R sidecarr:sidecarr /config /app

# Deliberately late: the release workflow sets this to the git tag so the app
# reports the version it was published as, and putting it here keeps a version
# bump from invalidating the cached dependency layer above. That matters most
# for the emulated arm64 build.
ARG VERSION=0.1.0
ENV SIDECARR_VERSION=${VERSION}

VOLUME ["/config"]
EXPOSE 9898
USER sidecarr

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys;p=os.environ.get('SIDECARR_PORT','9898');sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "sidecarr"]
