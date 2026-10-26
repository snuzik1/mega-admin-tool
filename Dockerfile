# MegaTool в контейнере. Linux-only, как и само приложение.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEGATOOL_HOME=/data

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY megatool ./megatool

RUN pip install --no-cache-dir . \
    && groupadd --system --gid 10001 megatool \
    && useradd --system --uid 10001 --gid megatool --home-dir /data --no-create-home megatool \
    && mkdir -p /data \
    && chown -R megatool:megatool /data \
    && chmod 700 /data

USER megatool
VOLUME ["/data"]
EXPOSE 8787

# /api/health публичен и не требует входа.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3)"

ENTRYPOINT ["python", "-m", "megatool", "--host", "0.0.0.0", "--port", "8787"]
CMD ["--no-browser"]
