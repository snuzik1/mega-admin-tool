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

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir . \
    && groupadd --system --gid 10001 megatool \
    && useradd --system --uid 10001 --gid megatool --home-dir /data --no-create-home megatool \
    && mkdir -p /data \
    && chown -R megatool:megatool /data \
    && chmod 700 /data

USER megatool
VOLUME ["/data"]
EXPOSE 8787

# /api/health публичен и не требует входа. Если в /data включён TLS,
# healthcheck идёт по HTTPS с проверкой только доступности (сертификат локальный).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,os,ssl,urllib.request; h=os.environ.get('MEGATOOL_HOME','/data'); c=json.load(open(h+'/config.json')); t=(c.get('server') or {}).get('tls') or {}; s='https' if t.get('enabled') else 'http'; urllib.request.urlopen(s+'://127.0.0.1:8787/api/health', context=ssl._create_unverified_context(), timeout=3)"

# Внутри контейнера bind на 0.0.0.0 нужен для docker-порта. Без TLS это
# разрешено только явным флагом: публикуйте порт как 127.0.0.1:8787:8787
# либо настройте TLS в /data (см. docs/OPERATIONS.md).
ENTRYPOINT ["python", "-m", "megatool", "--host", "0.0.0.0", "--port", "8787", "--allow-insecure-remote"]
CMD ["--no-browser"]
