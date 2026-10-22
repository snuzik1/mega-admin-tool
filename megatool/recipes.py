"""Библиотека приложений: готовые рецепты для развёртывания в один клик.

Три вида рецептов:

* ``compose``  — docker/podman-compose стек (БД, кэши, мониторинг, утилиты);
* ``manifest`` — набор Kubernetes-манифестов;
* ``config``   — набор конфигов для рабочей станции (nginx, systemd, ansible,
  prometheus, logrotate...). Ничего не запускает — просто раскладывает файлы,
  которые потом можно править встроенным редактором и раскатывать Ansible.

Шаблоны текстовые: ``{{KEY}}`` подставляется при развёртывании, а ``${KEY}``
остаётся для docker compose (значения уходят в файл ``.env``).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from .i18n import get_language

CATEGORIES = {
    "web": "Веб-серверы",
    "db": "Базы данных",
    "cache": "Кэш и брокеры",
    "storage": "Хранилища",
    "monitoring": "Мониторинг",
    "tools": "Инструменты",
    "k8s": "Kubernetes",
    "configs": "Конфиги и шаблоны",
}

KINDS = {"compose": "Docker/Podman Compose", "manifest": "Kubernetes-манифесты", "config": "Набор конфигов"}


def render(template: str, values: Dict[str, str]) -> Tuple[str, List[str]]:
    """Подставляет {{KEY}}. Возвращает текст и список незаполненных ключей."""
    import re

    missing: List[str] = []
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    for leftover in set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", out)):
        missing.append(leftover)
    return out, missing


def _p(key: str, label: str, default: str, hint: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "default": default, "hint": hint}


def _e(key: str, label: str, default: str = "", secret: bool = False, hint: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "default": default, "secret": secret, "hint": hint}


RECIPES: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------ #
    #  Веб-серверы
    # ------------------------------------------------------------------ #
    {
        "id": "nginx",
        "name": "Nginx + статика",
        "category": "web",
        "kind": "compose",
        "description": "Веб-сервер с готовым виртуальным хостом и каталогом статики. "
                       "Точка входа для фронтенда или проксирования.",
        "tags": ["web", "static", "proxy"],
        "docs": "https://hub.docker.com/_/nginx",
        "vars": [_p("NAME", "Имя контейнера", "nginx"), _p("PORT", "Внешний порт", "8080")],
        "ports": ["{{PORT}} → 80"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: nginx:1.27-alpine
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
      - ./conf.d:/etc/nginx/conf.d:ro
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
""",
            "conf.d/default.conf": """server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location = /healthz {
        access_log off;
        return 200 "ok\\n";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
""",
            "html/index.html": """<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>MegaTool</title></head>
<body style="font-family: monospace; background:#0a0a0b; color:#e9e9ea; padding:3rem">
<h1>Сервис запущен</h1>
<p>Файл создан рецептом MegaTool. Правьте <code>html/index.html</code> и <code>conf.d/default.conf</code>.</p>
</body>
</html>
""",
        },
    },
    {
        "id": "caddy",
        "name": "Caddy",
        "category": "web",
        "kind": "compose",
        "description": "Веб-сервер с автоматическим HTTPS и простым Caddyfile.",
        "tags": ["web", "proxy", "tls"],
        "docs": "https://hub.docker.com/_/caddy",
        "vars": [_p("NAME", "Имя контейнера", "caddy"), _p("PORT", "Внешний порт", "8081")],
        "ports": ["{{PORT}} → 80"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: caddy:2-alpine
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:80"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./site:/srv:ro
      - caddy_data:/data
volumes:
  caddy_data:
""",
            "Caddyfile": """:80 {
    root * /srv
    file_server
    encode gzip
    log {
        output stdout
    }
}
""",
            "site/index.html": "<!doctype html><meta charset=utf-8><h1>Caddy работает</h1>\n",
        },
    },
    # ------------------------------------------------------------------ #
    #  Базы данных
    # ------------------------------------------------------------------ #
    {
        "id": "postgres",
        "name": "PostgreSQL 16",
        "category": "db",
        "kind": "compose",
        "description": "Реляционная СУБД с healthcheck и локальным каталогом данных. "
                       "Пароль генерируется автоматически и лежит в .env.",
        "tags": ["sql", "database", "postgres"],
        "docs": "https://hub.docker.com/_/postgres",
        "vars": [_p("NAME", "Имя контейнера", "postgres"), _p("PORT", "Внешний порт", "5432")],
        "env": [
            _e("POSTGRES_PASSWORD", "Пароль суперпользователя", "__secret__", secret=True),
            _e("POSTGRES_USER", "Пользователь", "app"),
            _e("POSTGRES_DB", "База данных", "app"),
        ],
        "ports": ["{{PORT}} → 5432"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: postgres:16-alpine
    container_name: {{NAME}}
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "${PORT}:5432"
    volumes:
      - ./data:/var/lib/postgresql/data
      - ./init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
""",
            "init/01-init.sql": "-- Инициализация базы (выполняется один раз при создании тома)\nSELECT 1;\n",
        },
    },
    {
        "id": "mariadb",
        "name": "MariaDB 11",
        "category": "db",
        "kind": "compose",
        "description": "СУБД, совместимая с MySQL, с автоматической генерацией пароля root.",
        "tags": ["sql", "mysql", "database"],
        "docs": "https://hub.docker.com/_/mariadb",
        "vars": [_p("NAME", "Имя контейнера", "mariadb"), _p("PORT", "Внешний порт", "3306")],
        "env": [
            _e("MARIADB_ROOT_PASSWORD", "Пароль root", "__secret__", secret=True),
            _e("MARIADB_DATABASE", "База данных", "app"),
            _e("MARIADB_USER", "Пользователь", "app"),
            _e("MARIADB_PASSWORD", "Пароль пользователя", "__secret__", secret=True),
        ],
        "ports": ["{{PORT}} → 3306"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: mariadb:11
    container_name: {{NAME}}
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: ${MARIADB_ROOT_PASSWORD}
      MARIADB_DATABASE: ${MARIADB_DATABASE}
      MARIADB_USER: ${MARIADB_USER}
      MARIADB_PASSWORD: ${MARIADB_PASSWORD}
    ports:
      - "${PORT}:3306"
    volumes:
      - ./data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 15s
      timeout: 5s
      retries: 5
""",
        },
    },
    {
        "id": "redis",
        "name": "Redis 7",
        "category": "cache",
        "kind": "compose",
        "description": "Кэш и очередь в памяти, AOF-персистентность включена.",
        "tags": ["cache", "kv", "redis"],
        "docs": "https://hub.docker.com/_/redis",
        "vars": [_p("NAME", "Имя контейнера", "redis"), _p("PORT", "Внешний порт", "6379")],
        "env": [_e("REDIS_PASSWORD", "Пароль (requirepass)", "__secret__", secret=True)],
        "ports": ["{{PORT}} → 6379"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: redis:7-alpine
    container_name: {{NAME}}
    restart: unless-stopped
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    ports:
      - "${PORT}:6379"
    volumes:
      - ./data:/data
      - ./redis.conf:/usr/local/etc/redis/redis.conf:ro
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a $${REDIS_PASSWORD} ping | grep PONG"]
      interval: 15s
      timeout: 5s
      retries: 5
""",
            "redis.conf": """bind 0.0.0.0
protected-mode yes
appendonly yes
appendfsync everysec
save 900 1
requirepass {{REDIS_PASSWORD}}
maxmemory-policy noeviction
""",
        },
    },
    {
        "id": "mongodb",
        "name": "MongoDB 7",
        "category": "db",
        "kind": "compose",
        "description": "Документная СУБД с авторизацией и сохранением данных на диск.",
        "tags": ["nosql", "database", "mongo"],
        "docs": "https://hub.docker.com/_/mongo",
        "vars": [_p("NAME", "Имя контейнера", "mongo"), _p("PORT", "Внешний порт", "27017")],
        "env": [
            _e("MONGO_INITDB_ROOT_USERNAME", "Администратор", "root"),
            _e("MONGO_INITDB_ROOT_PASSWORD", "Пароль администратора", "__secret__", secret=True),
        ],
        "ports": ["{{PORT}} → 27017"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: mongo:7
    container_name: {{NAME}}
    restart: unless-stopped
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_INITDB_ROOT_USERNAME}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_INITDB_ROOT_PASSWORD}
    ports:
      - "${PORT}:27017"
    volumes:
      - ./data:/data/db
""",
        },
    },
    # ------------------------------------------------------------------ #
    #  Кэш, брокеры, хранилища
    # ------------------------------------------------------------------ #
    {
        "id": "rabbitmq",
        "name": "RabbitMQ 3",
        "category": "cache",
        "kind": "compose",
        "description": "Брокер сообщений с панелью управления.",
        "tags": ["queue", "amqp", "broker"],
        "docs": "https://hub.docker.com/_/rabbitmq",
        "vars": [
            _p("NAME", "Имя контейнера", "rabbitmq"),
            _p("PORT", "Порт AMQP", "5672"),
            _p("MGMT_PORT", "Порт панели", "15672"),
        ],
        "env": [
            _e("RABBITMQ_DEFAULT_USER", "Пользователь", "admin"),
            _e("RABBITMQ_DEFAULT_PASS", "Пароль", "__secret__", secret=True),
        ],
        "ports": ["{{PORT}} → 5672", "{{MGMT_PORT}} → 15672 (панель)"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: rabbitmq:3-management-alpine
    container_name: {{NAME}}
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: ${RABBITMQ_DEFAULT_USER}
      RABBITMQ_DEFAULT_PASS: ${RABBITMQ_DEFAULT_PASS}
    ports:
      - "${PORT}:5672"
      - "${MGMT_PORT}:15672"
    volumes:
      - ./data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 20s
      timeout: 10s
      retries: 5
""",
        },
    },
    {
        "id": "minio",
        "name": "MinIO (S3)",
        "category": "storage",
        "kind": "compose",
        "description": "S3-совместимое объектное хранилище: API и веб-консоль.",
        "tags": ["s3", "storage", "object"],
        "docs": "https://hub.docker.com/r/minio/minio",
        "vars": [
            _p("NAME", "Имя контейнера", "minio"),
            _p("PORT", "Порт API", "9000"),
            _p("CONSOLE_PORT", "Порт консоли", "9001"),
        ],
        "env": [
            _e("MINIO_ROOT_USER", "Логин", "minioadmin"),
            _e("MINIO_ROOT_PASSWORD", "Пароль", "__secret__", secret=True),
        ],
        "ports": ["{{PORT}} → 9000", "{{CONSOLE_PORT}} → 9001"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: minio/minio:latest
    container_name: {{NAME}}
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    ports:
      - "${PORT}:9000"
      - "${CONSOLE_PORT}:9001"
    volumes:
      - ./data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 20s
      timeout: 5s
      retries: 5
""",
        },
    },
    # ------------------------------------------------------------------ #
    #  Мониторинг
    # ------------------------------------------------------------------ #
    {
        "id": "prometheus",
        "name": "Prometheus",
        "category": "monitoring",
        "kind": "compose",
        "description": "Сбор метрик: сам себя и node-exporter. Файл конфигурации "
                       "prometheus.yml можно править в редакторе MegaTool.",
        "tags": ["metrics", "monitoring"],
        "docs": "https://hub.docker.com/r/prom/prometheus",
        "vars": [_p("NAME", "Имя контейнера", "prometheus"), _p("PORT", "Внешний порт", "9090")],
        "ports": ["{{PORT}} → 9090"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: prom/prometheus:latest
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./rules:/etc/prometheus/rules:ro
      - ./data:/prometheus
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d
""",
            "prometheus.yml": """global:
  scrape_interval: 15s
  evaluation_interval: 30s

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  - job_name: node
    static_configs:
      - targets: ["node-exporter:9100"]

  # Добавьте свои цели:
  # - job_name: app
  #   metrics_path: /metrics
  #   static_configs:
  #     - targets: ["10.0.0.5:8000"]
""",
            "rules/alerts.yml": """groups:
  - name: basic
    rules:
      - alert: InstanceDown
        expr: up == 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Цель недоступна {{ $labels.instance }}"

      - alert: HighMemory
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) > 0.9
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Память выше 90% на {{ $labels.instance }}"
""",
        },
    },
    {
        "id": "node-exporter",
        "name": "node-exporter",
        "category": "monitoring",
        "kind": "compose",
        "description": "Метрики хоста для Prometheus (CPU, память, диски, сеть).",
        "tags": ["metrics", "prometheus", "agent"],
        "docs": "https://hub.docker.com/r/prom/node-exporter",
        "vars": [_p("NAME", "Имя контейнера", "node-exporter"), _p("PORT", "Внешний порт", "9100")],
        "ports": ["{{PORT}} → 9100"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: prom/node-exporter:latest
    container_name: {{NAME}}
    restart: unless-stopped
    pid: host
    ports:
      - "${PORT}:9100"
    volumes:
      - /:/host:ro,rslave
    command:
      - --path.rootfs=/host
      - --collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)
""",
        },
    },
    {
        "id": "grafana",
        "name": "Grafana",
        "category": "monitoring",
        "kind": "compose",
        "description": "Дашборды и визуализация метрик. Пароль администратора — в .env.",
        "tags": ["dashboard", "monitoring", "metrics"],
        "docs": "https://hub.docker.com/r/grafana/grafana",
        "vars": [_p("NAME", "Имя контейнера", "grafana"), _p("PORT", "Внешний порт", "3000")],
        "env": [_e("GF_SECURITY_ADMIN_PASSWORD", "Пароль admin", "__secret__", secret=True)],
        "ports": ["{{PORT}} → 3000"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: grafana/grafana:latest
    container_name: {{NAME}}
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GF_SECURITY_ADMIN_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "${PORT}:3000"
    volumes:
      - ./data:/var/lib/grafana
      - ./provisioning:/etc/grafana/provisioning:ro
""",
            "provisioning/datasources/prometheus.yml": """apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
""",
        },
    },
    {
        "id": "cadvisor",
        "name": "cAdvisor",
        "category": "monitoring",
        "kind": "compose",
        "description": "Метрики контейнеров: потребление CPU, памяти, сети по каждому контейнеру.",
        "tags": ["docker", "metrics", "containers"],
        "docs": "https://hub.docker.com/r/gcr.io/cadvisor/cadvisor",
        "vars": [_p("NAME", "Имя контейнера", "cadvisor"), _p("PORT", "Внешний порт", "8082")],
        "ports": ["{{PORT}} → 8080"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: gcr.io/cadvisor/cadvisor:latest
    container_name: {{NAME}}
    restart: unless-stopped
    privileged: true
    ports:
      - "${PORT}:8080"
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
      - /dev/disk/:/dev/disk:ro
""",
        },
    },
    {
        "id": "uptime-kuma",
        "name": "Uptime Kuma",
        "category": "monitoring",
        "kind": "compose",
        "description": "Мониторинг доступности сервисов с уведомлениями и статус-страницей.",
        "tags": ["uptime", "availability", "monitoring"],
        "docs": "https://hub.docker.com/r/louislam/uptime-kuma",
        "vars": [_p("NAME", "Имя контейнера", "uptime-kuma"), _p("PORT", "Внешний порт", "3001")],
        "ports": ["{{PORT}} → 3001"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: louislam/uptime-kuma:1
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:3001"
    volumes:
      - ./data:/app/data
""",
        },
    },
    # ------------------------------------------------------------------ #
    #  Инструменты
    # ------------------------------------------------------------------ #
    {
        "id": "portainer",
        "name": "Portainer",
        "category": "tools",
        "kind": "compose",
        "description": "Веб-панель управления Docker/Podman-окружением.",
        "tags": ["docker", "ui", "management"],
        "docs": "https://hub.docker.com/r/portainer/portainer-ce",
        "vars": [_p("NAME", "Имя контейнера", "portainer"), _p("PORT", "Порт HTTPS", "9443")],
        "ports": ["{{PORT}} → 9443"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: portainer/portainer-ce:latest
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:9443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data:/data
""",
        },
    },
    {
        "id": "adminer",
        "name": "Adminer",
        "category": "tools",
        "kind": "compose",
        "description": "Лёгкий веб-клиент к PostgreSQL/MySQL/MariaDB.",
        "tags": ["sql", "ui", "database"],
        "docs": "https://hub.docker.com/_/adminer",
        "vars": [_p("NAME", "Имя контейнера", "adminer"), _p("PORT", "Внешний порт", "8083")],
        "ports": ["{{PORT}} → 8080"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: adminer:latest
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:8080"
    environment:
      ADMINER_DEFAULT_SERVER: postgres
""",
        },
    },
    {
        "id": "registry",
        "name": "Docker Registry",
        "category": "tools",
        "kind": "compose",
        "description": "Приватный реестр образов с веб-интерфейсом для просмотра.",
        "tags": ["registry", "images", "docker"],
        "docs": "https://hub.docker.com/_/registry",
        "vars": [
            _p("NAME", "Имя контейнера", "registry"),
            _p("PORT", "Порт реестра", "5001"),
            _p("UI_PORT", "Порт веб-интерфейса", "5002"),
        ],
        "ports": ["{{PORT}} → 5000", "{{UI_PORT}} → 8080"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: registry:2
    container_name: {{NAME}}
    restart: unless-stopped
    ports:
      - "${PORT}:5000"
    volumes:
      - ./data:/var/lib/registry
    environment:
      REGISTRY_STORAGE_DELETE_ENABLED: "true"

  {{NAME}}-ui:
    image: joxit/docker-registry-ui:latest
    container_name: {{NAME}}-ui
    restart: unless-stopped
    ports:
      - "${UI_PORT}:8080"
    environment:
      REGISTRY_TITLE: "Локальный реестр MegaTool"
      SINGLE_REGISTRY: "true"
      REGISTRY_URL: "http://localhost:${PORT}"
""",
        },
    },
    {
        "id": "watchtower",
        "name": "Watchtower",
        "category": "tools",
        "kind": "compose",
        "description": "Автоматическое обновление контейнеров при выходе новых образов.",
        "tags": ["updates", "docker", "automation"],
        "docs": "https://hub.docker.com/r/containrrr/watchtower",
        "vars": [_p("NAME", "Имя контейнера", "watchtower")],
        "ports": [],
        "warning": "Watchtower перезапускает контейнеры при обновлении образов — включайте осознанно.",
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}:
    image: containrrr/watchtower:latest
    container_name: {{NAME}}
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      WATCHTOWER_CLEANUP: "true"
      WATCHTOWER_SCHEDULE: "0 0 4 * * *"
""",
        },
    },
    {
        "id": "dev-stack",
        "name": "Стек для разработки",
        "category": "tools",
        "kind": "compose",
        "description": "Готовый набор: nginx + PostgreSQL + Redis. Удобная стартовая точка "
                       "для локальной разработки проекта.",
        "tags": ["dev", "stack", "postgres", "redis", "nginx"],
        "docs": "",
        "vars": [
            _p("NAME", "Префикс имён", "dev"),
            _p("WEB_PORT", "Порт веб-сервера", "8084"),
            _p("DB_PORT", "Порт базы", "5433"),
            _p("REDIS_PORT", "Порт Redis", "6380"),
        ],
        "env": [
            _e("POSTGRES_PASSWORD", "Пароль PostgreSQL", "__secret__", secret=True),
            _e("POSTGRES_USER", "Пользователь БД", "dev"),
            _e("POSTGRES_DB", "База", "dev"),
            _e("REDIS_PASSWORD", "Пароль Redis", "__secret__", secret=True),
        ],
        "ports": ["{{WEB_PORT}} → 80", "{{DB_PORT}} → 5432", "{{REDIS_PORT}} → 6379"],
        "files": {
            "docker-compose.yml": """services:
  {{NAME}}-web:
    image: nginx:1.27-alpine
    container_name: {{NAME}}-web
    restart: unless-stopped
    ports:
      - "${WEB_PORT}:80"
    volumes:
      - ./site:/usr/share/nginx/html:ro
    depends_on:
      {{NAME}}-db:
        condition: service_healthy

  {{NAME}}-db:
    image: postgres:16-alpine
    container_name: {{NAME}}-db
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "${DB_PORT}:5432"
    volumes:
      - ./data/db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  {{NAME}}-redis:
    image: redis:7-alpine
    container_name: {{NAME}}-redis
    restart: unless-stopped
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    ports:
      - "${REDIS_PORT}:6379"
    volumes:
      - ./data/redis:/data
""",
            "site/index.html": "<!doctype html><meta charset=utf-8><h1>dev-stack запущен</h1>\n",
        },
    },
    # ------------------------------------------------------------------ #
    #  Kubernetes-манифесты
    # ------------------------------------------------------------------ #
    {
        "id": "k8s-namespace",
        "name": "Namespace + квоты",
        "category": "k8s",
        "kind": "manifest",
        "description": "Namespace с ResourceQuota и LimitRange — базовая гигиена кластера.",
        "tags": ["namespace", "quota", "limits"],
        "docs": "https://kubernetes.io/docs/concepts/policy/resource-quotas/",
        "vars": [_p("NAME", "Namespace", "app")],
        "ports": [],
        "files": {
            "00-namespace.yaml": """apiVersion: v1
kind: Namespace
metadata:
  name: {{NAME}}
  labels:
    managed-by: megatool
""",
            "01-quota.yaml": """apiVersion: v1
kind: ResourceQuota
metadata:
  name: {{NAME}}-quota
  namespace: {{NAME}}
spec:
  hard:
    requests.cpu: "4"
    requests.memory: 8Gi
    limits.cpu: "8"
    limits.memory: 16Gi
    persistentvolumeclaims: "10"
    pods: "40"
""",
            "02-limitrange.yaml": """apiVersion: v1
kind: LimitRange
metadata:
  name: {{NAME}}-limits
  namespace: {{NAME}}
spec:
  limits:
    - type: Container
      default:
        cpu: 250m
        memory: 256Mi
      defaultRequest:
        cpu: 100m
        memory: 128Mi
""",
        },
        "apply_hint": "kubectl apply -f <каталог>",
    },
    {
        "id": "k8s-web-app",
        "name": "Веб-приложение (Deployment + Service + Ingress)",
        "category": "k8s",
        "kind": "manifest",
        "description": "Типовой веб-сервис: 2 реплики, пробы, лимиты, Service и Ingress.",
        "tags": ["deployment", "service", "ingress"],
        "docs": "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/",
        "vars": [
            _p("NAME", "Имя приложения", "web"),
            _p("NAMESPACE", "Namespace", "default"),
            _p("IMAGE", "Образ", "nginx:1.27-alpine"),
            _p("REPLICAS", "Реплики", "2"),
            _p("HOST", "Домен для Ingress", "app.example.com"),
        ],
        "ports": [],
        "files": {
            "deployment.yaml": """apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{NAME}}
  namespace: {{NAMESPACE}}
  labels:
    app: {{NAME}}
spec:
  replicas: {{REPLICAS}}
  selector:
    matchLabels:
      app: {{NAME}}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: {{NAME}}
    spec:
      containers:
        - name: {{NAME}}
          image: {{IMAGE}}
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 20
            periodSeconds: 20
""",
            "service.yaml": """apiVersion: v1
kind: Service
metadata:
  name: {{NAME}}
  namespace: {{NAMESPACE}}
spec:
  type: ClusterIP
  selector:
    app: {{NAME}}
  ports:
    - name: http
      port: 80
      targetPort: 80
""",
            "ingress.yaml": """apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{NAME}}
  namespace: {{NAMESPACE}}
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: 16m
spec:
  ingressClassName: nginx
  rules:
    - host: {{HOST}}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{NAME}}
                port:
                  number: 80
""",
        },
        "apply_hint": "kubectl apply -f <каталог>",
    },
    {
        "id": "k8s-postgres",
        "name": "PostgreSQL StatefulSet",
        "category": "k8s",
        "kind": "manifest",
        "description": "StatefulSet с PVC и headless-сервисом. Пароль вынесен в Secret.",
        "tags": ["statefulset", "postgres", "storage"],
        "docs": "https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/",
        "vars": [
            _p("NAME", "Имя", "postgres"),
            _p("NAMESPACE", "Namespace", "default"),
            _p("STORAGE", "Размер тома", "10Gi"),
        ],
        "env": [_e("POSTGRES_PASSWORD", "Пароль", "__secret__", secret=True)],
        "ports": [],
        "files": {
            "secret.yaml": """apiVersion: v1
kind: Secret
metadata:
  name: {{NAME}}-secret
  namespace: {{NAMESPACE}}
type: Opaque
stringData:
  POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
  POSTGRES_DB: "app"
  POSTGRES_USER: "app"
""",
            "statefulset.yaml": """apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{NAME}}
  namespace: {{NAMESPACE}}
spec:
  serviceName: {{NAME}}
  replicas: 1
  selector:
    matchLabels:
      app: {{NAME}}
  template:
    metadata:
      labels:
        app: {{NAME}}
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          ports:
            - containerPort: 5432
          envFrom:
            - secretRef:
                name: {{NAME}}-secret
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "2"
              memory: 2Gi
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: {{STORAGE}}
""",
            "service.yaml": """apiVersion: v1
kind: Service
metadata:
  name: {{NAME}}
  namespace: {{NAMESPACE}}
spec:
  clusterIP: None
  selector:
    app: {{NAME}}
  ports:
    - port: 5432
      targetPort: 5432
""",
        },
        "apply_hint": "kubectl apply -f <каталог>",
    },
    {
        "id": "k8s-cronjob",
        "name": "CronJob (регламентная задача)",
        "category": "k8s",
        "kind": "manifest",
        "description": "Задача по расписанию с историей запусков и ограничениями ресурсов.",
        "tags": ["cronjob", "schedule", "automation"],
        "docs": "https://kubernetes.io/docs/concepts/workloads/controllers/cronjob/",
        "vars": [
            _p("NAME", "Имя", "backup"),
            _p("NAMESPACE", "Namespace", "default"),
            _p("SCHEDULE", "Расписание (cron)", "0 3 * * *"),
            _p("IMAGE", "Образ", "alpine:3.20"),
        ],
        "ports": [],
        "files": {
            "cronjob.yaml": """apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{NAME}}
  namespace: {{NAMESPACE}}
spec:
  schedule: "{{SCHEDULE}}"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: {{NAME}}
              image: {{IMAGE}}
              command: ["/bin/sh", "-c", "echo \"MegaTool: задача запущена $(date)\"; # ваша команда"]
              resources:
                requests:
                  cpu: 50m
                  memory: 64Mi
                limits:
                  cpu: 500m
                  memory: 256Mi
""",
        },
        "apply_hint": "kubectl apply -f <каталог>",
    },
    # ------------------------------------------------------------------ #
    #  Библиотека конфигов рабочей станции
    # ------------------------------------------------------------------ #
    {
        "id": "cfg-nginx-site",
        "name": "Конфиг nginx: сайт + upstream",
        "category": "configs",
        "kind": "config",
        "description": "Виртуальный хост nginx с балансировкой на несколько бэкендов, "
                       "кэшем статики, gzip и защитными заголовками.",
        "tags": ["nginx", "site", "upstream"],
        "docs": "https://nginx.org/ru/docs/http/ngx_http_upstream_module.html",
        "vars": [_p("DOMAIN", "Домен", "app.example.com"), _p("BACKEND", "Бэкенд", "127.0.0.1:8000")],
        "ports": [],
        "files": {
            "site.conf": """upstream {{DOMAIN}}_backend {
    least_conn;
    server {{BACKEND}} weight=1 max_fails=3 fail_timeout=15s;
    # server 10.0.0.12:8000 weight=2 max_fails=3 fail_timeout=15s;
    keepalive 32;
}

server {
    listen 80;
    server_name {{DOMAIN}};

    access_log /var/log/nginx/{{DOMAIN}}.access.log;
    error_log  /var/log/nginx/{{DOMAIN}}.error.log warn;

    client_max_body_size 32m;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml image/svg+xml;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location /static/ {
        alias /var/www/{{DOMAIN}}/static/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://{{DOMAIN}}_backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_read_timeout 60s;
        proxy_next_upstream error timeout http_502 http_503 http_504;
    }
}
""",
            "README.md": """# Как применить

1. Скопируйте `site.conf` в `/etc/nginx/conf.d/` (или `sites-available`).
2. Проверьте конфигурацию: `nginx -t`
3. Перечитайте конфигурацию без простоя: `nginx -s reload`

MegaTool умеет проверить файл кнопкой «Проверить» в редакторе (если nginx установлен).
""",
        },
    },
    {
        "id": "cfg-systemd",
        "name": "Конфиг systemd: юнит сервиса",
        "category": "configs",
        "kind": "config",
        "description": "Юнит-файл для сервиса приложения с отдельным пользователем, "
                       "ограничениями безопасности и ротацией логов.",
        "tags": ["systemd", "service", "linux"],
        "docs": "https://www.freedesktop.org/software/systemd/man/systemd.service.html",
        "vars": [
            _p("APP", "Имя сервиса", "myapp"),
            _p("USER", "Пользователь", "app"),
            _p("WORKDIR", "Рабочий каталог", "/opt/myapp"),
            _p("EXEC", "Команда запуска", "/opt/myapp/bin/start"),
        ],
        "ports": [],
        "files": {
            "{{APP}}.service": """[Unit]
Description={{APP}} service
Documentation=https://example.com/{{APP}}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{USER}}
Group={{USER}}
WorkingDirectory={{WORKDIR}}
EnvironmentFile=-{{WORKDIR}}/.env
ExecStart={{EXEC}}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM

# Ограничения безопасности
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={{WORKDIR}}
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
""",
            "{{APP}}.env.example": """# Переменные окружения сервиса (скопируйте в {{WORKDIR}}/.env)
APP_ENV=production
PORT=8000
LOG_LEVEL=info
""",
            "README.md": """# Как применить

```bash
sudo cp {{APP}}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now {{APP}}
systemctl status {{APP}}
journalctl -u {{APP}} -f
```
""",
        },
    },
    {
        "id": "cfg-ansible-role",
        "name": "Ansible: каркас роли",
        "category": "configs",
        "kind": "config",
        "description": "Полноценный каркас роли (tasks, handlers, defaults, templates, meta) "
                       "с примером установки пакетов и деплоя юнита systemd.",
        "tags": ["ansible", "role", "automation"],
        "docs": "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse_roles.html",
        "vars": [
            _p("ROLE", "Имя роли", "webapp"),
            _p("APP", "Имя приложения", "myapp"),
        ],
        "ports": [],
        "files": {
            "roles/{{ROLE}}/tasks/main.yml": """---
- name: Установить зависимости
  ansible.builtin.apt:
    name: "{{ {{ROLE}}_packages }}"
    state: present
    update_cache: true
  become: true
  when: ansible_os_family == "Debian"

- name: Создать пользователя приложения
  ansible.builtin.user:
    name: "{{ {{ROLE}}_user }}"
    system: true
    shell: /usr/sbin/nologin
    create_home: false
  become: true

- name: Создать каталоги
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ {{ROLE}}_user }}"
    group: "{{ {{ROLE}}_user }}"
    mode: "0755"
  loop:
    - "{{ {{ROLE}}_dir }}"
    - "{{ {{ROLE}}_dir }}/logs"
  become: true

- name: Разложить юнит systemd
  ansible.builtin.template:
    src: {{APP}}.service.j2
    dest: "/etc/systemd/system/{{APP}}.service"
    mode: "0644"
  notify: перезапустить {{APP}}
  become: true

- name: Включить сервис
  ansible.builtin.systemd:
    name: "{{APP}}"
    enabled: true
    state: started
    daemon_reload: true
  become: true
""",
            "roles/{{ROLE}}/defaults/main.yml": """---
{{ROLE}}_packages:
  - curl
  - ca-certificates
{{ROLE}}_user: "{{APP}}"
{{ROLE}}_dir: "/opt/{{APP}}"
{{ROLE}}_port: 8000
""",
            "roles/{{ROLE}}/handlers/main.yml": """---
- name: перезапустить {{APP}}
  ansible.builtin.systemd:
    name: "{{APP}}"
    state: restarted
    daemon_reload: true
""",
            "roles/{{ROLE}}/templates/{{APP}}.service.j2": """[Unit]
Description={{APP}} (управляется Ansible {{ role_name | default('') }})
After=network-online.target

[Service]
Type=simple
User={{ '{{' }} {{ROLE}}_user {{ '}}' }}
WorkingDirectory={{ '{{' }} {{ROLE}}_dir {{ '}}' }}
ExecStart={{ '{{' }} {{ROLE}}_dir {{ '}}' }}/bin/start
Environment=PORT={{ '{{' }} {{ROLE}}_port {{ '}}' }}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
""",
            "roles/{{ROLE}}/meta/main.yml": """---
galaxy_info:
  role_name: {{ROLE}}
  author: MegaTool
  description: Роль приложения {{APP}}
  license: MIT
  min_ansible_version: "2.14"
  platforms:
    - name: Debian
    - name: Ubuntu
dependencies: []
""",
            "playbook.yml": """---
- name: Развернуть {{APP}}
  hosts: all
  become: true
  roles:
    - {{ROLE}}
""",
        },
    },
    {
        "id": "cfg-ansible-baseline",
        "name": "Ansible: базовый плейбук сервера",
        "category": "configs",
        "kind": "config",
        "description": "Обновление пакетов, часовой пояс, пользователь, SSH-ключи, "
                       "fail2ban, ufw — типовой «первый день» сервера.",
        "tags": ["ansible", "security", "baseline"],
        "docs": "https://docs.ansible.com/",
        "vars": [
            _p("USER", "Администратор", "deploy"),
            _p("TIMEZONE", "Часовой пояс", "Europe/Moscow"),
        ],
        "ports": [],
        "files": {
            "baseline.yml": """---
- name: Базовая настройка сервера
  hosts: all
  become: true
  gather_facts: true

  vars:
    admin_user: "{{USER}}"
    timezone: "{{TIMEZONE}}"
    ssh_port: 22
    allowed_ssh_keys: []   # список публичных ключей администратора

  tasks:
    - name: Обновить кэш пакетов и установить обновления
      ansible.builtin.apt:
        upgrade: safe
        update_cache: true
        cache_valid_time: 3600
      when: ansible_os_family == "Debian"

    - name: Задать часовой пояс
      community.general.timezone:
        name: "{{ timezone }}"

    - name: Создать администратора
      ansible.builtin.user:
        name: "{{ admin_user }}"
        groups: sudo
        append: true
        shell: /bin/bash
        create_home: true

    - name: Разложить SSH-ключи
      ansible.posix.authorized_key:
        user: "{{ admin_user }}"
        key: "{{ item }}"
        state: present
      loop: "{{ allowed_ssh_keys }}"
      when: allowed_ssh_keys | length > 0

    - name: Установить полезные утилиты
      ansible.builtin.apt:
        name: [htop, ncdu, jq, curl, ufw, fail2ban, unattended-upgrades]
        state: present

    - name: Открыть только SSH в firewall
      community.general.ufw:
        rule: allow
        port: "{{ ssh_port }}"
        proto: tcp
      register: ufw_rule

    - name: Включить ufw
      community.general.ufw:
        state: enabled
        policy: deny
        direction: incoming

    - name: Включить автобезопасные обновления
      ansible.builtin.service:
        name: unattended-upgrades
        state: started
        enabled: true

    - name: Итог
      ansible.builtin.debug:
        msg: "{{ inventory_hostname }}: {{ ansible_distribution }} {{ ansible_distribution_version }}, "
             "свободно на / {{ (ansible_mounts | selectattr('mount', 'equalto', '/') | list | first).size_available | human_readable }}"
""",
            "inventory.example.ini": """[servers]
# srv1 ansible_host=203.0.113.10 ansible_user={{USER}}
# srv2 ansible_host=203.0.113.11 ansible_user={{USER}}

[servers:vars]
ansible_python_interpreter=/usr/bin/python3
""",
        },
    },
    {
        "id": "cfg-prometheus",
        "name": "Prometheus: сбор и алерты",
        "category": "configs",
        "kind": "config",
        "description": "Файл сбора метрик с нескольких хостов и набор правил алертов "
                       "(диск, память, load, недоступность).",
        "tags": ["prometheus", "metrics", "alerts"],
        "docs": "https://prometheus.io/docs/prometheus/latest/configuration/configuration/",
        "vars": [_p("HOST1", "Первый хост", "10.0.0.10")],
        "ports": [],
        "files": {
            "prometheus.yml": """global:
  scrape_interval: 15s
  scrape_timeout: 10s
  evaluation_interval: 30s
  external_labels:
    cluster: prod

rule_files:
  - /etc/prometheus/rules/*.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: node
    static_configs:
      - targets:
          - "{{HOST1}}:9100"
        labels:
          env: prod

  - job_name: blackbox-http
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - "https://example.com"
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115
""",
            "rules/node-alerts.yml": """groups:
  - name: node
    rules:
      - alert: NodeDown
        expr: up{job="node"} == 0
        for: 3m
        labels: { severity: critical }
        annotations:
          summary: "Хост {{ $labels.instance }} недоступен"

      - alert: DiskWillFillIn4Hours
        expr: predict_linear(node_filesystem_avail_bytes{mountpoint="/"}[1h], 4*3600) < 0
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "Диск заполнится в течение 4 часов на {{ $labels.instance }}"

      - alert: HighLoad
        expr: node_load1 / count by (instance) (node_cpu_seconds_total{mode="idle"}) > 2
        for: 15m
        labels: { severity: warning }
        annotations:
          summary: "Высокий load average на {{ $labels.instance }}"
""",
        },
    },
    {
        "id": "cfg-logrotate",
        "name": "Конфиг logrotate",
        "category": "configs",
        "kind": "config",
        "description": "Ротация логов приложения с сжатием и перезагрузкой сервиса.",
        "tags": ["logrotate", "logs", "linux"],
        "docs": "https://linux.die.net/man/8/logrotate",
        "vars": [_p("APP", "Имя приложения", "myapp"), _p("DAYS", "Хранить дней", "14")],
        "ports": [],
        "files": {
            "{{APP}}": """/var/log/{{APP}}/*.log {
    daily
    rotate {{DAYS}}
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    dateext
    dateformat -%Y%m%d-%s
    create 0640 root adm
    sharedscripts
    postrotate
        /usr/bin/systemctl reload {{APP}}.service > /dev/null 2>&1 || true
    endscript
}
""",
            "README.md": """# Как применить

```bash
sudo cp {{APP}} /etc/logrotate.d/{{APP}}
sudo chown root:root /etc/logrotate.d/{{APP}}
sudo chmod 644 /etc/logrotate.d/{{APP}}
sudo logrotate -d /etc/logrotate.d/{{APP}}   # проверка без выполнения
sudo logrotate -f /etc/logrotate.d/{{APP}}   # принудительная ротация
```
""",
        },
    },
    {
        "id": "cfg-ssh-hardening",
        "name": "Ужесточение SSH",
        "category": "configs",
        "kind": "config",
        "description": "Безопасный sshd_config: только ключи, без root-входа, "
                       "ограничение попыток и keep-alive.",
        "tags": ["ssh", "security", "hardening"],
        "docs": "https://www.ssh.com/academy/ssh/sshd_config",
        "vars": [_p("PORT", "Порт SSH", "22"), _p("GROUP", "Группа доступа", "sshusers")],
        "ports": [],
        "files": {
            "99-hardening.conf": """# MegaTool: файл кладётся в /etc/ssh/sshd_config.d/ и перекрывает основные настройки
Port {{PORT}}
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no
UsePAM yes

AllowGroups {{GROUP}}
MaxAuthTries 3
MaxSessions 5
LoginGraceTime 20
ClientAliveInterval 300
ClientAliveCountMax 2

# Только безопасные алгоритмы
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com

X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no

LogLevel VERBOSE
""",
            "README.md": """# Порядок действий (важно не потерять доступ)

```bash
# 1. Убедитесь, что вход по ключу работает и группа существует
sudo groupadd -f {{GROUP}}
sudo usermod -aG {{GROUP}} <ваш_пользователь}}

# 2. Проверьте конфигурацию
sudo sshd -t -f /etc/ssh/sshd_config.d/99-hardening.conf

# 3. Примените
sudo cp 99-hardening.conf /etc/ssh/sshd_config.d/
sudo systemctl reload ssh

# 4. Оставьте открытой текущую сессию, проверьте вход в новой
# Если что-то не так: восстановите доступ через консоль провайдера и удалите файл
```
""",
        },
    },
]


# --------------------------------------------------------------------------- #
#  Английская локализация каталога
# --------------------------------------------------------------------------- #

#: Переводы названий, описаний и подписей переменных (язык en).
#: Значения по умолчанию (default) — данные, их не переводим.
_LOC_EN: Dict[str, Dict[str, Any]] = {
    # Веб-серверы
    "nginx": {
        "name": "Nginx + static site",
        "description": "Web server with a ready-made virtual host and a static directory. "
                       "Entry point for a frontend or reverse proxy.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    "caddy": {
        "name": "Caddy",
        "description": "Web server with automatic HTTPS and a minimal Caddyfile.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    # Базы данных
    "postgres": {
        "name": "PostgreSQL 16",
        "description": "Relational database with a healthcheck and a local data directory. "
                       "The password is generated automatically and stored in .env.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
        "env": {
            "POSTGRES_PASSWORD": {"label": "Superuser password"},
            "POSTGRES_USER": {"label": "User"},
            "POSTGRES_DB": {"label": "Database"},
        },
    },
    "mariadb": {
        "name": "MariaDB 11",
        "description": "MySQL-compatible database with automatic root password generation.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
        "env": {
            "MARIADB_ROOT_PASSWORD": {"label": "Root password"},
            "MARIADB_DATABASE": {"label": "Database"},
            "MARIADB_USER": {"label": "User"},
            "MARIADB_PASSWORD": {"label": "User password"},
        },
    },
    "redis": {
        "name": "Redis 7",
        "description": "In-memory cache and queue with AOF persistence enabled.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
        "env": {
            "REDIS_PASSWORD": {"label": "Password (requirepass)"},
        },
    },
    "mongodb": {
        "name": "MongoDB 7",
        "description": "Document database with authentication and on-disk data persistence.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
        "env": {
            "MONGO_INITDB_ROOT_USERNAME": {"label": "Administrator"},
            "MONGO_INITDB_ROOT_PASSWORD": {"label": "Administrator password"},
        },
    },
    # Кэш, брокеры, хранилища
    "rabbitmq": {
        "name": "RabbitMQ 3",
        "description": "Message broker with a management UI.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "AMQP port"},
            "MGMT_PORT": {"label": "Management port"},
        },
        "env": {
            "RABBITMQ_DEFAULT_USER": {"label": "User"},
            "RABBITMQ_DEFAULT_PASS": {"label": "Password"},
        },
    },
    "minio": {
        "name": "MinIO (S3)",
        "description": "S3-compatible object storage: API and web console.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "API port"},
            "CONSOLE_PORT": {"label": "Console port"},
        },
        "env": {
            "MINIO_ROOT_USER": {"label": "Login"},
            "MINIO_ROOT_PASSWORD": {"label": "Password"},
        },
    },
    # Мониторинг
    "prometheus": {
        "name": "Prometheus",
        "description": "Metrics collection: itself and node-exporter. The prometheus.yml "
                       "configuration file can be edited in the MegaTool editor.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    "node-exporter": {
        "name": "node-exporter",
        "description": "Host metrics for Prometheus (CPU, memory, disks, network).",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    "grafana": {
        "name": "Grafana",
        "description": "Dashboards and metrics visualization. The admin password is stored in .env.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
        "env": {
            "GF_SECURITY_ADMIN_PASSWORD": {"label": "Admin password"},
        },
    },
    "cadvisor": {
        "name": "cAdvisor",
        "description": "Container metrics: CPU, memory and network usage for every container.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    "uptime-kuma": {
        "name": "Uptime Kuma",
        "description": "Service uptime monitoring with notifications and a status page.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    # Инструменты
    "portainer": {
        "name": "Portainer",
        "description": "Web UI for managing a Docker/Podman environment.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "HTTPS port"},
        },
    },
    "adminer": {
        "name": "Adminer",
        "description": "Lightweight web client for PostgreSQL/MySQL/MariaDB.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "External port"},
        },
    },
    "registry": {
        "name": "Docker Registry",
        "description": "Private image registry with a web UI for browsing.",
        "vars": {
            "NAME": {"label": "Container name"},
            "PORT": {"label": "Registry port"},
            "UI_PORT": {"label": "Web UI port"},
        },
    },
    "watchtower": {
        "name": "Watchtower",
        "description": "Automatic container updates when new images are released.",
        "vars": {
            "NAME": {"label": "Container name"},
        },
    },
    "dev-stack": {
        "name": "Development stack",
        "description": "Ready-made bundle: nginx + PostgreSQL + Redis. A convenient starting "
                       "point for local project development.",
        "vars": {
            "NAME": {"label": "Name prefix"},
            "WEB_PORT": {"label": "Web server port"},
            "DB_PORT": {"label": "Database port"},
            "REDIS_PORT": {"label": "Redis port"},
        },
        "env": {
            "POSTGRES_PASSWORD": {"label": "PostgreSQL password"},
            "POSTGRES_USER": {"label": "Database user"},
            "POSTGRES_DB": {"label": "Database"},
            "REDIS_PASSWORD": {"label": "Redis password"},
        },
    },
    # Kubernetes-манифесты
    "k8s-namespace": {
        "name": "Namespace + quotas",
        "description": "Namespace with ResourceQuota and LimitRange — basic cluster hygiene.",
        "vars": {
            "NAME": {"label": "Namespace"},
        },
    },
    "k8s-web-app": {
        "name": "Web application (Deployment + Service + Ingress)",
        "description": "Typical web service: 2 replicas, probes, limits, Service and Ingress.",
        "vars": {
            "NAME": {"label": "Application name"},
            "NAMESPACE": {"label": "Namespace"},
            "IMAGE": {"label": "Image"},
            "REPLICAS": {"label": "Replicas"},
            "HOST": {"label": "Ingress domain"},
        },
    },
    "k8s-postgres": {
        "name": "PostgreSQL StatefulSet",
        "description": "StatefulSet with a PVC and a headless service. The password is kept in a Secret.",
        "vars": {
            "NAME": {"label": "Name"},
            "NAMESPACE": {"label": "Namespace"},
            "STORAGE": {"label": "Volume size"},
        },
        "env": {
            "POSTGRES_PASSWORD": {"label": "Password"},
        },
    },
    "k8s-cronjob": {
        "name": "CronJob (scheduled task)",
        "description": "Scheduled task with run history and resource limits.",
        "vars": {
            "NAME": {"label": "Name"},
            "NAMESPACE": {"label": "Namespace"},
            "SCHEDULE": {"label": "Schedule (cron)"},
            "IMAGE": {"label": "Image"},
        },
    },
    # Конфиги и шаблоны
    "cfg-nginx-site": {
        "name": "nginx config: site + upstream",
        "description": "nginx virtual host with load balancing across several backends, "
                       "static file caching, gzip and security headers.",
        "vars": {
            "DOMAIN": {"label": "Domain"},
            "BACKEND": {"label": "Backend"},
        },
    },
    "cfg-systemd": {
        "name": "systemd config: service unit",
        "description": "Unit file for an application service with a dedicated user, "
                       "security hardening and log rotation.",
        "vars": {
            "APP": {"label": "Service name"},
            "USER": {"label": "User"},
            "WORKDIR": {"label": "Working directory"},
            "EXEC": {"label": "Start command"},
        },
    },
    "cfg-ansible-role": {
        "name": "Ansible: role skeleton",
        "description": "A complete role skeleton (tasks, handlers, defaults, templates, meta) "
                       "with an example of installing packages and deploying a systemd unit.",
        "vars": {
            "ROLE": {"label": "Role name"},
            "APP": {"label": "Application name"},
        },
    },
    "cfg-ansible-baseline": {
        "name": "Ansible: server baseline playbook",
        "description": "Package updates, timezone, admin user, SSH keys, fail2ban, ufw — "
                       "the typical server first-day setup.",
        "vars": {
            "USER": {"label": "Administrator"},
            "TIMEZONE": {"label": "Timezone"},
        },
    },
    "cfg-prometheus": {
        "name": "Prometheus: scraping and alerts",
        "description": "Metrics scraping config for several hosts and a set of alert rules "
                       "(disk, memory, load, downtime).",
        "vars": {
            "HOST1": {"label": "First host"},
        },
    },
    "cfg-logrotate": {
        "name": "logrotate config",
        "description": "Application log rotation with compression and service reload.",
        "vars": {
            "APP": {"label": "Application name"},
            "DAYS": {"label": "Days to keep"},
        },
    },
    "cfg-ssh-hardening": {
        "name": "SSH hardening",
        "description": "Hardened sshd_config: key-only login, no root access, "
                       "limited attempts and keep-alive.",
        "vars": {
            "PORT": {"label": "SSH port"},
            "GROUP": {"label": "Access group"},
        },
    },
}

#: Английские ярлыки категорий и видов рецептов.
_CATEGORIES_EN: Dict[str, str] = {
    "web": "Web servers",
    "db": "Databases",
    "cache": "Caches & brokers",
    "storage": "Storage",
    "monitoring": "Monitoring",
    "tools": "Tools",
    "k8s": "Kubernetes",
    "configs": "Configs & templates",
}

_KINDS_EN: Dict[str, str] = {
    "compose": "Docker/Podman Compose",
    "manifest": "Kubernetes manifests",
    "config": "Config bundle",
}


def localize(recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Глубокая копия рецепта с переводом на язык интерфейса (en).

    Значения по умолчанию, шаблоны файлов и идентификаторы не переводятся.
    """
    result = copy.deepcopy(recipe)
    if get_language() != "en":
        return result
    entry = _LOC_EN.get(str(result.get("id", "")))
    if not entry:
        return result
    if entry.get("name"):
        result["name"] = entry["name"]
    if entry.get("description"):
        result["description"] = entry["description"]
    for field in ("vars", "env"):
        translations = entry.get(field) or {}
        for item in result.get(field) or []:
            patch = translations.get(item.get("key", ""))
            if not patch:
                continue
            if patch.get("label"):
                item["label"] = patch["label"]
            if patch.get("hint"):
                item["hint"] = patch["hint"]
    return result


def categories() -> Dict[str, str]:
    """Ярлыки категорий каталога на языке интерфейса."""
    return dict(_CATEGORIES_EN if get_language() == "en" else CATEGORIES)


def kinds() -> Dict[str, str]:
    """Ярлыки видов рецептов на языке интерфейса."""
    return dict(_KINDS_EN if get_language() == "en" else KINDS)


def list_recipes(query: str = "", category: str = "", kind: str = "") -> List[Dict[str, Any]]:
    """Список рецептов без тяжёлых полей (шаблоны не отдаём целиком)."""
    query = (query or "").strip().lower()
    category_labels = categories()
    kind_labels = kinds()
    items = []
    for source in RECIPES:
        recipe = localize(source)
        if category and recipe["category"] != category:
            continue
        if kind and recipe["kind"] != kind:
            continue
        if query:
            haystack = " ".join([
                recipe["id"], recipe["name"], recipe["description"],
                " ".join(recipe.get("tags", [])),
            ]).lower()
            if query not in haystack:
                continue
        items.append({
            "id": recipe["id"],
            "name": recipe["name"],
            "category": recipe["category"],
            "category_label": category_labels.get(recipe["category"], recipe["category"]),
            "kind": recipe["kind"],
            "kind_label": kind_labels.get(recipe["kind"], recipe["kind"]),
            "description": recipe["description"],
            "tags": recipe.get("tags", []),
            "docs": recipe.get("docs", ""),
            "vars": recipe.get("vars", []),
            "env": [{k: v for k, v in item.items() if k != "default" and not (k == "secret" and not v)}
                    for item in recipe.get("env", [])],
            "env_all": recipe.get("env", []),
            "ports": recipe.get("ports", []),
            "files": sorted(recipe.get("files", {}).keys()),
            "warning": recipe.get("warning", ""),
            "apply_hint": recipe.get("apply_hint", ""),
        })
    return items


def get_recipe(recipe_id: str) -> Optional[Dict[str, Any]]:
    for recipe in RECIPES:
        if recipe["id"] == recipe_id:
            return localize(recipe)
    return None


def catalog_stats() -> Dict[str, Any]:
    by_category: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for recipe in RECIPES:
        by_category[recipe["category"]] = by_category.get(recipe["category"], 0) + 1
        by_kind[recipe["kind"]] = by_kind.get(recipe["kind"], 0) + 1
    return {
        "total": len(RECIPES),
        "by_category": by_category,
        "by_kind": by_kind,
        "categories": categories(),
        "kinds": kinds(),
    }
