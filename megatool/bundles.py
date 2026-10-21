"""Наборы конфигов: именованные пакеты файлов (compose, nginx, Ansible, .env...).

Хранилище — каталог ``BUNDLES_DIR`` (по умолчанию ``~/.megatool/bundles``)::

    bundles/
        <id>/
            bundle.json     — манифест набора
            files/          — сами файлы (относительные пути)

Набор создаётся из готового шаблона (``PRESETS``) или пустым, редактируется
через API и выгружается одним ZIP-архивом. Любой путь проверяется: выйти за
пределы каталога ``files`` набора нельзя.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any

from .changes import audit
from .config import BUNDLES_DIR, Config
from .i18n import t
from .util import atomic_write, ensure_dir, iso, now_ts, read_json, slugify, write_json

MANIFEST_NAME = "bundle.json"
FILES_DIR = "files"

#: Ограничения, чтобы набор не превратился в файлопомойку.
MAX_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024

#: Идентификатор набора: только безопасные символы, без «..» и разделителей.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _fail(key: str, **params: Any) -> dict[str, Any]:
    """Ответ об ошибке с локализованным текстом (ключи словаря bundles.*)."""
    return {"ok": False, "error": t(key, **params)}


# --------------------------------------------------------------------------- #
#  Шаблоны наборов
# --------------------------------------------------------------------------- #

PRESETS: list[dict[str, Any]] = [
    {
        "id": "dev-server",
        "title": "Сервер для разработки",
        "title_en": "Development server",
        "description": "Nginx со статикой, PostgreSQL и Redis в compose, "
                       "плюс Ansible-плейбук для установки Docker и запуска стека.",
        "description_en": "Nginx with a static site, PostgreSQL and Redis in compose, "
                          "plus an Ansible playbook to install Docker and start the stack.",
        "files": {
            "docker-compose.yml": """# MegaTool preset "dev-server": local development stack.
# Copy .env.example to .env before the first start.
services:
  web:
    image: nginx:1.27-alpine
    container_name: dev-web
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-8080}:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro

  postgres:
    image: postgres:16-alpine
    container_name: dev-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-dev}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-dev}
      POSTGRES_DB: ${POSTGRES_DB:-dev}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dev} -d ${POSTGRES_DB:-dev}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: dev-redis
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    ports:
      - "${REDIS_PORT:-6379}:6379"
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Example application service: uncomment and point "build" to your project.
  # api:
  #   build: .
  #   container_name: dev-api
  #   restart: unless-stopped
  #   environment:
  #     DATABASE_URL: postgres://${POSTGRES_USER:-dev}:${POSTGRES_PASSWORD:-dev}@postgres:5432/${POSTGRES_DB:-dev}
  #     REDIS_URL: redis://redis:6379/0
  #   ports:
  #     - "${API_PORT:-8000}:8000"
  #   depends_on:
  #     postgres:
  #       condition: service_healthy
  #     redis:
  #       condition: service_healthy

volumes:
  pgdata:
  redisdata:
""",
            ".env.example": """# Example environment file: copy it to .env and change the secrets.
# docker compose reads .env automatically from this directory.
WEB_PORT=8080
API_PORT=8000
POSTGRES_PORT=5432
POSTGRES_USER=dev
POSTGRES_PASSWORD=change-me
POSTGRES_DB=dev
REDIS_PORT=6379
""",
            "nginx/default.conf": """# MegaTool preset "dev-server": static site plus a commented proxy example.
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript image/svg+xml;

    location = /healthz {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy to the application container (uncomment after enabling "api" in compose).
    # location /api/ {
    #     proxy_pass http://api:8000/;
    #     proxy_set_header Host $host;
    #     proxy_set_header X-Real-IP $remote_addr;
    #     proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # }
}
""",
            "html/index.html": """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>dev-server</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #101014; color: #eceff4; padding: 3rem; }
    code { background: #1d1f27; padding: 2px 6px; }
  </style>
</head>
<body>
  <h1>dev-server is running</h1>
  <p>Stack: nginx + PostgreSQL + Redis (created by the MegaTool preset).</p>
  <p>Edit <code>html/index.html</code> and reload the page.</p>
</body>
</html>
""",
            "ansible/playbook.yml": """---
# MegaTool preset "dev-server": prepare a host and start the compose stack.
- name: Configure the development server
  hosts: all
  become: true
  vars:
    app_dir: /opt/dev-server
    bundle_dir: "{{ playbook_dir }}/.."

  tasks:
    - name: Install base packages
      ansible.builtin.apt:
        name:
          - ca-certificates
          - curl
        state: present
        update_cache: true
      when: ansible_os_family == "Debian"

    - name: Install Docker packages
      ansible.builtin.apt:
        name:
          - docker.io
          - docker-compose-plugin
        state: present
      when: ansible_os_family == "Debian"
      notify: restart docker

    - name: Make sure Docker is enabled and running
      ansible.builtin.service:
        name: docker
        state: started
        enabled: true

    - name: Add the connecting user to the docker group
      ansible.builtin.user:
        name: "{{ ansible_user | default('root') }}"
        groups: docker
        append: true
      when: ansible_user is defined and ansible_user != "root"

    - name: Create the application directory
      ansible.builtin.file:
        path: "{{ app_dir }}"
        state: directory
        mode: "0755"

    - name: Copy bundle files to the host
      ansible.builtin.copy:
        src: "{{ bundle_dir }}/{{ item }}"
        dest: "{{ app_dir }}/{{ item }}"
        mode: preserve
      loop:
        - docker-compose.yml
        - .env.example
        - nginx/default.conf
        - html/index.html

    - name: Create .env from the example (kept on re-runs)
      ansible.builtin.copy:
        src: "{{ app_dir }}/.env.example"
        dest: "{{ app_dir }}/.env"
        remote_src: true
        force: false
        mode: "0600"

    - name: Start the compose stack
      ansible.builtin.command:
        cmd: docker compose up -d
        chdir: "{{ app_dir }}"
      register: compose_up
      changed_when: "'Started' in compose_up.stdout or 'Created' in compose_up.stdout"

    - name: Show the result
      ansible.builtin.debug:
        msg: "dev-server up: {{ compose_up.stdout_lines | default([]) }}"

  handlers:
    - name: restart docker
      ansible.builtin.service:
        name: docker
        state: restarted
""",
            "ansible/inventory.ini": """; MegaTool preset "dev-server": example inventory.
; Replace the host address and user with your own.
[dev]
dev-server ansible_host=203.0.113.10 ansible_user=deploy

[dev:vars]
ansible_python_interpreter=/usr/bin/python3
""",
            "README.md": """# Набор «Сервер для разработки» / Development server bundle

Стек для локальной разработки: nginx (статика) + PostgreSQL + Redis.
Ansible-плейбук ставит Docker и поднимает compose на сервере.

Local development stack: nginx (static) + PostgreSQL + Redis.
The Ansible playbook installs Docker and starts the compose project on a host.

## Состав / Contents

- `docker-compose.yml` — сервисы web, postgres, redis и пример api в комментарии;
- `.env.example` — пример переменных окружения (скопируйте в `.env`);
- `nginx/default.conf` — конфиг виртуального хоста;
- `html/index.html` — стартовая страница;
- `ansible/playbook.yml`, `ansible/inventory.ini` — установка Docker и запуск стека.

## Запуск / Usage

```bash
cp .env.example .env
docker compose up -d
```

```bash
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml
```
""",
        },
    },
    {
        "id": "web-nginx",
        "title": "Веб-сервер nginx",
        "title_en": "Nginx web server",
        "description": "Виртуальный хост nginx со статическим сайтом, gzip, кэшем "
                       "статики и защитными заголовками; Ansible ставит и настраивает nginx.",
        "description_en": "An nginx virtual host with a static site, gzip, static caching "
                          "and security headers; Ansible installs and configures nginx.",
        "files": {
            "nginx/site.conf": """# MegaTool preset "web-nginx": virtual host for a static site.
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    root /var/www/example.com;
    index index.html;

    access_log /var/log/nginx/example.com.access.log;
    error_log  /var/log/nginx/example.com.error.log warn;

    server_tokens off;
    client_max_body_size 32m;

    gzip on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/json application/javascript image/svg+xml;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location = /healthz {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok";
    }

    location ~* \\.(css|js|png|jpe?g|gif|svg|ico|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public";
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ =404;
    }
}
""",
            "html/index.html": """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>example.com</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 3rem; background: #f6f7f9; color: #1b1d22; }
    main { max-width: 640px; margin: 0 auto; }
  </style>
</head>
<body>
  <main>
    <h1>It works</h1>
    <p>Static site published by the MegaTool "web-nginx" preset.</p>
    <p>Replace this file with your own content.</p>
  </main>
</body>
</html>
""",
            "ansible/playbook.yml": """---
# MegaTool preset "web-nginx": install nginx and publish a static site.
- name: Configure the web server
  hosts: all
  become: true
  vars:
    site_name: example.com
    site_root: "/var/www/{{ site_name }}"
    bundle_dir: "{{ playbook_dir }}/.."

  tasks:
    - name: Install nginx
      ansible.builtin.apt:
        name: nginx
        state: present
        update_cache: true
      when: ansible_os_family == "Debian"

    - name: Create the site directory
      ansible.builtin.file:
        path: "{{ site_root }}"
        state: directory
        owner: www-data
        group: www-data
        mode: "0755"

    - name: Publish the index page
      ansible.builtin.copy:
        src: "{{ bundle_dir }}/html/index.html"
        dest: "{{ site_root }}/index.html"
        owner: www-data
        group: www-data
        mode: "0644"

    - name: Install the virtual host
      ansible.builtin.copy:
        src: "{{ bundle_dir }}/nginx/site.conf"
        dest: "/etc/nginx/sites-available/{{ site_name }}.conf"
        owner: root
        group: root
        mode: "0644"
      notify: reload nginx

    - name: Enable the virtual host
      ansible.builtin.file:
        src: "/etc/nginx/sites-available/{{ site_name }}.conf"
        dest: "/etc/nginx/sites-enabled/{{ site_name }}.conf"
        state: link
      notify: reload nginx

    - name: Remove the default site
      ansible.builtin.file:
        path: /etc/nginx/sites-enabled/default
        state: absent
      notify: reload nginx

    - name: Make sure nginx is enabled and running
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: true

  handlers:
    - name: reload nginx
      ansible.builtin.service:
        name: nginx
        state: reloaded
""",
            "ansible/inventory.ini": """; MegaTool preset "web-nginx": example inventory.
; Replace the host address and user with your own.
[web]
web-01 ansible_host=203.0.113.10 ansible_user=deploy

[web:vars]
ansible_python_interpreter=/usr/bin/python3
""",
            "README.md": """# Набор «Веб-сервер nginx» / Nginx web server bundle

Виртуальный хост nginx со статическим сайтом, gzip, кэшем статики и
защитными заголовками. Ansible-плейбук ставит nginx и публикует сайт.

An nginx virtual host with a static site, gzip, static caching and security
headers. The Ansible playbook installs nginx and publishes the site.

## Состав / Contents

- `nginx/site.conf` — виртуальный хост (замените `example.com` на свой домен);
- `html/index.html` — стартовая страница;
- `ansible/playbook.yml`, `ansible/inventory.ini` — установка и настройка.

## Запуск / Usage

```bash
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml
```

Перед запуском проверьте домен и адрес хоста в `nginx/site.conf` и
`ansible/inventory.ini`. / Check the domain and host before running.
""",
        },
    },
    {
        "id": "docker-host",
        "title": "Docker-хост",
        "title_en": "Docker host",
        "description": "Ansible-плейбук устанавливает Docker и compose-плагин, включает "
                       "ротацию логов демона и добавляет пользователя в группу docker.",
        "description_en": "An Ansible playbook that installs Docker and the compose plugin, "
                          "enables daemon log rotation and adds users to the docker group.",
        "files": {
            "ansible/playbook.yml": """---
# MegaTool preset "docker-host": install Docker Engine with the compose plugin.
- name: Prepare the Docker host
  hosts: all
  become: true
  vars:
    # Users allowed to run docker without sudo, for example ["deploy"].
    docker_users: []
    bundle_dir: "{{ playbook_dir }}/.."

  tasks:
    - name: Install Docker packages
      ansible.builtin.apt:
        name:
          - ca-certificates
          - curl
          - docker.io
          - docker-compose-plugin
        state: present
        update_cache: true
      when: ansible_os_family == "Debian"

    - name: Configure the Docker daemon
      ansible.builtin.copy:
        src: "{{ bundle_dir }}/docker/daemon.json"
        dest: /etc/docker/daemon.json
        owner: root
        group: root
        mode: "0644"
      notify: restart docker

    - name: Make sure Docker is enabled and running
      ansible.builtin.service:
        name: docker
        state: started
        enabled: true

    - name: Allow users to run docker without sudo
      ansible.builtin.user:
        name: "{{ item }}"
        groups: docker
        append: true
      loop: "{{ docker_users }}"

    - name: Check the installation
      ansible.builtin.command:
        cmd: docker compose version
      register: compose_version
      changed_when: false

    - name: Show the installed version
      ansible.builtin.debug:
        msg: "{{ compose_version.stdout }}"

  handlers:
    - name: restart docker
      ansible.builtin.service:
        name: docker
        state: restarted
""",
            "docker/daemon.json": """{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true
}
""",
            "compose/example.yml": """# Example compose project for the prepared host.
# Start it with: docker compose -f compose/example.yml up -d
services:
  whoami:
    image: traefik/whoami:latest
    container_name: whoami
    restart: unless-stopped
    ports:
      - "8081:80"
""",
            "README.md": """# Набор «Docker-хост» / Docker host bundle

Ansible-плейбук ставит Docker Engine и плагин compose, раскладывает
`docker/daemon.json` с ротацией логов и добавляет пользователей в группу docker.

The playbook installs Docker Engine and the compose plugin, deploys
`docker/daemon.json` with log rotation and adds users to the docker group.

## Состав / Contents

- `ansible/playbook.yml` — установка и настройка хоста;
- `docker/daemon.json` — параметры демона (ротация логов, live-restore);
- `compose/example.yml` — пример проекта для проверки.

## Запуск / Usage

```bash
ansible-playbook -i inventory.ini ansible/playbook.yml
docker compose -f compose/example.yml up -d
```

Список пользователей задаётся переменной `docker_users`. /
Set users in the `docker_users` variable.
""",
        },
    },
    {
        "id": "monitoring",
        "title": "Мониторинг (Prometheus + Grafana)",
        "title_en": "Monitoring (Prometheus + Grafana)",
        "description": "Compose-стек мониторинга: Prometheus, Grafana и node-exporter "
                       "с готовым сбором метрик хоста.",
        "description_en": "A monitoring compose stack: Prometheus, Grafana and node-exporter "
                          "with host metrics collection ready to use.",
        "files": {
            "docker-compose.yml": """# MegaTool preset "monitoring": Prometheus + Grafana + node-exporter.
services:
  prometheus:
    image: prom/prometheus:latest
    container_name: monitoring-prometheus
    restart: unless-stopped
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d
    ports:
      - "${PROMETHEUS_PORT:-9090}:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus

  grafana:
    image: grafana/grafana:latest
    container_name: monitoring-grafana
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "${GRAFANA_PORT:-3000}:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on:
      - prometheus

  node-exporter:
    image: prom/node-exporter:latest
    container_name: monitoring-node-exporter
    restart: unless-stopped
    pid: host
    command:
      - --path.rootfs=/host
    ports:
      - "${NODE_EXPORTER_PORT:-9100}:9100"
    volumes:
      - /:/host:ro,rslave

volumes:
  prometheus-data:
  grafana-data:
""",
            "prometheus/prometheus.yml": """# MegaTool preset "monitoring": minimal Prometheus configuration.
global:
  scrape_interval: 15s
  evaluation_interval: 30s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  - job_name: node
    static_configs:
      - targets: ["node-exporter:9100"]

  # Add more targets, for example:
  # - job_name: app
  #   metrics_path: /metrics
  #   static_configs:
  #     - targets: ["10.0.0.10:8000"]
""",
            "README.md": """# Набор «Мониторинг (Prometheus + Grafana)» / Monitoring bundle

Compose-стек: Prometheus собирает метрики, node-exporter отдаёт метрики хоста,
Grafana показывает дашборды.

Compose stack: Prometheus scrapes metrics, node-exporter exposes host metrics
and Grafana renders dashboards.

## Запуск / Usage

```bash
docker compose up -d
```

- Prometheus — <http://localhost:9090>
- Grafana — <http://localhost:3000> (admin / admin по умолчанию)

После первого входа добавьте источник данных Prometheus с адресом
`http://prometheus:9090` в Grafana. / Add the `http://prometheus:9090`
data source in Grafana after the first login.

Порты и пароль задаются переменными `PROMETHEUS_PORT`, `GRAFANA_PORT`,
`GRAFANA_PASSWORD`. / Ports and the password are configured by those variables.
""",
        },
    },
    {
        "id": "k8s-app",
        "title": "Приложение в Kubernetes",
        "title_en": "Kubernetes application",
        "description": "Namespace, Deployment с двумя репликами, Service и Ingress — "
                       "готовый каркас приложения для кластера.",
        "description_en": "A namespace, a two-replica Deployment, a Service and an Ingress — "
                          "a ready application skeleton for a cluster.",
        "files": {
            "k8s/namespace.yaml": """apiVersion: v1
kind: Namespace
metadata:
  name: demo
  labels:
    managed-by: megatool
""",
            "k8s/deployment.yaml": """apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-app
  namespace: demo
  labels:
    app: demo-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: demo-app
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: demo-app
    spec:
      containers:
        - name: demo-app
          image: nginx:1.27-alpine
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 256Mi
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
            "k8s/service.yaml": """apiVersion: v1
kind: Service
metadata:
  name: demo-app
  namespace: demo
spec:
  type: ClusterIP
  selector:
    app: demo-app
  ports:
    - name: http
      port: 80
      targetPort: 80
""",
            "k8s/ingress.yaml": """apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: demo-app
  namespace: demo
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: 16m
spec:
  ingressClassName: nginx
  rules:
    - host: demo.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: demo-app
                port:
                  number: 80
""",
            "README.md": """# Набор «Приложение в Kubernetes» / Kubernetes application bundle

Готовый каркас приложения: namespace `demo`, Deployment из двух реплик,
Service и Ingress. Образ — `nginx:1.27-alpine`, его можно заменить на свой.

A ready application skeleton: the `demo` namespace, a two-replica Deployment,
a Service and an Ingress. The image is `nginx:1.27-alpine`; replace it with yours.

## Состав / Contents

- `k8s/namespace.yaml` — пространство имён;
- `k8s/deployment.yaml` — Deployment с пробами и лимитами;
- `k8s/service.yaml` — ClusterIP-сервис;
- `k8s/ingress.yaml` — Ingress для `demo.example.com`.

## Запуск / Usage

```bash
kubectl apply -f k8s/
kubectl -n demo get pods,svc,ingress
```

Замените домен в `k8s/ingress.yaml` и образ в `k8s/deployment.yaml`
перед применением. / Replace the host and image before applying.
""",
        },
    },
]

#: Быстрый доступ к шаблонам по идентификатору.
PRESET_MAP = {preset["id"]: preset for preset in PRESETS}


# --------------------------------------------------------------------------- #
#  Хранилище
# --------------------------------------------------------------------------- #

class BundleStore:
    """Файловое хранилище наборов конфигов.

    Каталог хранения по умолчанию — ``BUNDLES_DIR`` из megatool.config.
    Если конфиг лежит в другом месте (например, во временном каталоге тестов),
    наборы живут рядом с ним — это сохраняет изоляцию и переносимость.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.dir = cfg.path.parent / BUNDLES_DIR.name
        self.lock = threading.RLock()
        ensure_dir(self.dir)

    # ------------------------------------------------------------------ #
    #  Пути и безопасность
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_rel(path: str) -> str:
        """Проверяет относительный путь файла внутри набора.

        Запрещены пустые пути, ``..``, абсолютные пути, ``~``, ``:`` и
        управляющие символы. Возвращает нормализованный путь через ``/``.
        При нарушении бросает ValueError с ключом словаря.
        """
        value = str(path or "").strip().replace("\\", "/")
        if (not value or "\x00" in value or any(ord(ch) < 32 for ch in value)
                or value.startswith("/") or "~" in value or ":" in value):
            raise ValueError("bundles.badPath")
        parts = [part for part in value.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("bundles.badPath")
        return "/".join(parts)

    def _bundle_dir(self, bid: str) -> Path | None:
        """Каталог набора по идентификатору или None, если id небезопасен."""
        value = str(bid or "").strip().lower()
        if not _ID_RE.match(value):
            return None
        base = self.dir.resolve()
        target = (self.dir / value).resolve()
        if target != base and base not in target.parents:
            return None
        return target

    def _file_path(self, bundle_dir: Path, rel: str) -> Path:
        """Путь файла; гарантирует, что результат лежит внутри files-каталога."""
        clean = self._safe_rel(rel)
        base = (bundle_dir / FILES_DIR).resolve()
        target = (base / clean).resolve()
        if target != base and base not in target.parents:
            raise ValueError("bundles.badPath")
        return target

    # ------------------------------------------------------------------ #
    #  Манифест и список файлов
    # ------------------------------------------------------------------ #

    @staticmethod
    def _manifest_path(bundle_dir: Path) -> Path:
        return bundle_dir / MANIFEST_NAME

    def _load(self, bundle_dir: Path) -> dict[str, Any] | None:
        data = read_json(self._manifest_path(bundle_dir), default=None)
        if not isinstance(data, dict):
            return None
        if data.get("id") != bundle_dir.name:
            return None
        return data

    def _files(self, bundle_dir: Path) -> list[dict[str, Any]]:
        """Список файлов набора с размерами и временем изменения."""
        base = bundle_dir / FILES_DIR
        if not base.is_dir():
            return []
        items: list[dict[str, Any]] = []
        try:
            paths = sorted(base.rglob("*"))
        except OSError:
            return []
        for path in paths:
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({
                "path": path.relative_to(base).as_posix(),
                "size": stat.st_size,
                "modified": iso(stat.st_mtime),
            })
        return items

    def _touch(self, bundle_dir: Path, manifest: dict[str, Any]) -> None:
        """Обновляет список файлов и метку времени в манифесте."""
        manifest["files"] = [item["path"] for item in self._files(bundle_dir)]
        manifest["updated_ts"] = now_ts()
        write_json(self._manifest_path(bundle_dir), manifest)

    def _summary(self, bundle_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        files = self._files(bundle_dir)
        return {
            "id": manifest["id"],
            "name": manifest.get("name") or manifest["id"],
            "description": manifest.get("description", ""),
            "preset": manifest.get("preset", ""),
            "created": iso(float(manifest.get("created_ts") or 0)),
            "updated": iso(float(manifest.get("updated_ts") or manifest.get("created_ts") or 0)),
            "created_ts": float(manifest.get("created_ts") or 0),
            "updated_ts": float(manifest.get("updated_ts") or manifest.get("created_ts") or 0),
            "file_count": len(files),
            "size": sum(item["size"] for item in files),
        }

    # ------------------------------------------------------------------ #
    #  Список, чтение, создание
    # ------------------------------------------------------------------ #

    def list(self) -> list[dict[str, Any]]:
        """Краткий список наборов (без содержимого файлов), новые сверху."""
        items: list[dict[str, Any]] = []
        with self.lock:
            try:
                children = sorted(self.dir.iterdir())
            except OSError:
                return []
            for child in children:
                if not child.is_dir():
                    continue
                manifest = self._load(child)
                if manifest is None:
                    continue
                items.append(self._summary(child, manifest))
        items.sort(key=lambda item: item["updated_ts"], reverse=True)
        return items

    def get(self, bid: str) -> dict[str, Any]:
        """Полные данные набора: метаданные и список файлов."""
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            manifest = self._load(bundle_dir)
            if manifest is None:
                return _fail("bundles.notFound", id=bid)
            summary = self._summary(bundle_dir, manifest)
            summary["files"] = self._files(bundle_dir)
            summary["ok"] = True
            return summary

    def create(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Создаёт набор; preset копирует файлы шаблона."""
        data = payload or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return _fail("bundles.needName")
        description = str(data.get("description") or "").strip()
        preset_id = str(data.get("preset") or "").strip()
        preset = PRESET_MAP.get(preset_id) if preset_id else None
        if preset_id and preset is None:
            return _fail("bundles.presetNotFound", preset=preset_id)

        with self.lock:
            bid = self._unique_id(slugify(name, "bundle"))
            bundle_dir = self.dir / bid
            files_dir = bundle_dir / FILES_DIR
            try:
                ensure_dir(files_dir)
                if preset:
                    for rel, content in preset["files"].items():
                        destination = self._file_path(bundle_dir, rel)
                        ensure_dir(destination.parent)
                        atomic_write(destination, content)
                stamp = now_ts()
                manifest = {
                    "id": bid,
                    "name": name,
                    "description": description,
                    "preset": preset["id"] if preset else "",
                    "created_ts": stamp,
                    "updated_ts": stamp,
                    "files": [],
                }
                self._touch(bundle_dir, manifest)
            except (OSError, ValueError) as exc:
                shutil.rmtree(bundle_dir, ignore_errors=True)
                if isinstance(exc, ValueError):
                    return _fail(str(exc))
                return _fail("bundles.writeFailed", error=str(exc))

        audit("bundle.create", target=bid,
              detail=f"{name} (preset={preset['id'] if preset else '—'})")
        return self.get(bid)

    def _unique_id(self, base: str) -> str:
        """Slug с суффиксом -2, -3…, если такой каталог уже занят."""
        bid = base
        counter = 1
        while (self.dir / bid).exists():
            counter += 1
            bid = f"{base}-{counter}"
        return bid

    def save_meta(self, bid: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Обновляет имя и/или описание набора."""
        data = patch or {}
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            manifest = self._load(bundle_dir)
            if manifest is None:
                return _fail("bundles.notFound", id=bid)

            changed = False
            if "name" in data:
                name = str(data.get("name") or "").strip()
                if not name:
                    return _fail("bundles.needName")
                if name != manifest.get("name"):
                    manifest["name"] = name
                    changed = True
            if "description" in data:
                description = str(data.get("description") or "").strip()
                if description != manifest.get("description"):
                    manifest["description"] = description
                    changed = True
            if not changed:
                return self.get(bid)
            manifest["updated_ts"] = now_ts()
            try:
                write_json(self._manifest_path(bundle_dir), manifest)
            except OSError as exc:
                return _fail("bundles.writeFailed", error=str(exc))

        audit("bundle.update", target=bid, detail=str(manifest.get("name", "")))
        return self.get(bid)

    def delete(self, bid: str) -> dict[str, Any]:
        """Удаляет набор вместе со всеми файлами."""
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            if self._load(bundle_dir) is None:
                return _fail("bundles.notFound", id=bid)
            try:
                shutil.rmtree(bundle_dir)
            except OSError as exc:
                return _fail("bundles.deleteFailed", error=str(exc))
        audit("bundle.delete", target=bid)
        return {"ok": True, "id": bid}

    # ------------------------------------------------------------------ #
    #  Файлы
    # ------------------------------------------------------------------ #

    def read_file(self, bid: str, path: str) -> dict[str, Any]:
        """Читает файл набора как текст UTF-8."""
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            if self._load(bundle_dir) is None:
                return _fail("bundles.notFound", id=bid)
            try:
                target = self._file_path(bundle_dir, path)
            except ValueError as exc:
                return _fail(str(exc))
            if not target.is_file():
                return _fail("bundles.fileNotFound", path=str(path))
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
                stat = target.stat()
            except OSError as exc:
                return _fail("bundles.readFailed", error=str(exc))
            rel = target.relative_to((bundle_dir / FILES_DIR).resolve()).as_posix()
        return {
            "ok": True,
            "path": rel,
            "content": content,
            "size": stat.st_size,
            "modified": iso(stat.st_mtime),
        }

    def write_file(self, bid: str, path: str, content: Any) -> dict[str, Any]:
        """Записывает файл набора (создаёт при необходимости)."""
        text = content if isinstance(content, str) else str(content or "")
        size = len(text.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            return _fail("bundles.fileTooBig", mb=MAX_FILE_BYTES // (1024 * 1024))

        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            manifest = self._load(bundle_dir)
            if manifest is None:
                return _fail("bundles.notFound", id=bid)
            try:
                target = self._file_path(bundle_dir, path)
            except ValueError as exc:
                return _fail(str(exc))
            base = (bundle_dir / FILES_DIR).resolve()
            if not target.exists() and len(self._files(bundle_dir)) >= MAX_FILES:
                return _fail("bundles.tooManyFiles", limit=MAX_FILES)
            try:
                ensure_dir(target.parent)
                atomic_write(target, text)
                self._touch(bundle_dir, manifest)
                stat = target.stat()
            except OSError as exc:
                return _fail("bundles.writeFailed", error=str(exc))
            rel = target.relative_to(base).as_posix()

        audit("bundle.file.write", target=f"{bid}/{rel}", detail=f"{len(text)} символов")
        return {
            "ok": True,
            "path": rel,
            "size": stat.st_size,
            "modified": iso(stat.st_mtime),
        }

    def add_file(self, bid: str, path: str) -> dict[str, Any]:
        """Создаёт пустой файл в наборе."""
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            manifest = self._load(bundle_dir)
            if manifest is None:
                return _fail("bundles.notFound", id=bid)
            try:
                target = self._file_path(bundle_dir, path)
            except ValueError as exc:
                return _fail(str(exc))
            base = (bundle_dir / FILES_DIR).resolve()
            rel = target.relative_to(base).as_posix()
            if target.exists():
                return _fail("bundles.fileExists", path=rel)
            if len(self._files(bundle_dir)) >= MAX_FILES:
                return _fail("bundles.tooManyFiles", limit=MAX_FILES)
            try:
                ensure_dir(target.parent)
                atomic_write(target, "")
                self._touch(bundle_dir, manifest)
                stat = target.stat()
            except OSError as exc:
                return _fail("bundles.writeFailed", error=str(exc))

        audit("bundle.file.write", target=f"{bid}/{rel}", detail="создан пустой файл")
        return {
            "ok": True,
            "path": rel,
            "size": stat.st_size,
            "modified": iso(stat.st_mtime),
        }

    def delete_file(self, bid: str, path: str) -> dict[str, Any]:
        """Удаляет файл набора и пустые каталоги после него."""
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            manifest = self._load(bundle_dir)
            if manifest is None:
                return _fail("bundles.notFound", id=bid)
            try:
                target = self._file_path(bundle_dir, path)
            except ValueError as exc:
                return _fail(str(exc))
            base = (bundle_dir / FILES_DIR).resolve()
            if not target.is_file():
                return _fail("bundles.fileNotFound", path=str(path))
            rel = target.relative_to(base).as_posix()
            try:
                target.unlink()
                self._prune_empty(base, target.parent)
                self._touch(bundle_dir, manifest)
            except OSError as exc:
                return _fail("bundles.deleteFailed", error=str(exc))

        audit("bundle.file.delete", target=f"{bid}/{rel}")
        return {"ok": True, "path": rel}

    @staticmethod
    def _prune_empty(base: Path, directory: Path) -> None:
        """Убирает пустые каталоги вверх до files-каталога (не трогая его)."""
        current = directory
        while current != base and base in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    # ------------------------------------------------------------------ #
    #  Шаблоны и экспорт
    # ------------------------------------------------------------------ #

    @staticmethod
    def presets() -> list[dict[str, Any]]:
        """Список шаблонов без содержимого файлов."""
        return [
            {
                "id": preset["id"],
                "title": preset["title"],
                "title_en": preset["title_en"],
                "description": preset["description"],
                "description_en": preset["description_en"],
                "files": sorted(preset["files"].keys()),
            }
            for preset in PRESETS
        ]

    def export_zip(self, bid: str) -> bytes | dict[str, Any]:
        """Собирает ZIP (bundle.json + files/...) в памяти.

        При ошибке возвращает словарь {"ok": False, "error": ...}.
        """
        with self.lock:
            bundle_dir = self._bundle_dir(bid)
            if bundle_dir is None:
                return _fail("bundles.badId")
            manifest = self._load(bundle_dir)
            if manifest is None:
                return _fail("bundles.notFound", id=bid)
            files = self._files(bundle_dir)
            archive_manifest = dict(manifest)
            archive_manifest["files"] = [item["path"] for item in files]
            base = bundle_dir / FILES_DIR
            buffer = io.BytesIO()
            try:
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(
                        MANIFEST_NAME,
                        json.dumps(archive_manifest, ensure_ascii=False, indent=2),
                    )
                    for item in files:
                        archive.write(base / item["path"], f"{FILES_DIR}/{item['path']}")
            except (OSError, ValueError) as exc:
                return _fail("bundles.exportFailed", error=str(exc))
            result = buffer.getvalue()

        audit("bundle.export", target=manifest["id"], detail=f"ZIP, файлов: {len(files)}")
        return result


__all__ = ["BundleStore", "PRESETS", "PRESET_MAP", "MAX_FILES", "MAX_FILE_BYTES"]
