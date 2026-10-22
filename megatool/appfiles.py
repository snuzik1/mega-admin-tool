"""Рабочее пространство и редактор конфигов.

Все файловые операции проходят через песочницу: путь обязан находиться внутри
домашнего каталога MegaTool или дополнительных корней, заданных в настройках.
Так редактор остаётся полезным (nginx, ansible, k8s-манифесты), но не превращается
в инструмент правки всей файловой системы.

Здесь же — проверка синтаксиса (YAML/JSON/INI/env/nginx/systemd/Dockerfile)
и галерея шаблонов для быстрого создания файлов.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from .config import APPS_DIR, CONFIGS_DIR, HOME_DIR, Config
from .i18n import t
from .util import IS_WINDOWS, human_bytes, is_within, iso, write_text

TEXT_EXTENSIONS = {
    ".yml", ".yaml", ".json", ".ini", ".cfg", ".conf", ".env", ".txt", ".md", ".log",
    ".service", ".sh", ".ps1", ".sql", ".tf", ".toml", ".xml", ".html", ".css", ".js",
    ".py", ".tpl", ".j2", ".example", ".properties", ".rules", ".dockerignore", ".gitignore",
}
MAX_READ = 512 * 1024


@dataclass
class FileNode:
    name: str
    path: str
    type: str                     # file | dir
    size: int = 0
    mtime: float = 0.0
    children: List['FileNode'] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "size": self.size,
            "size_human": human_bytes(self.size) if self.type == "file" else "",
            "mtime": self.mtime,
            "mtime_iso": iso(self.mtime) if self.mtime else None,
            "children": [child.to_dict() for child in self.children],
        }


class Workspace:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ #
    def roots(self) -> List[Dict[str, Any]]:
        items = [
            {"id": "home", "label": t("appfiles.rootHome"), "path": str(HOME_DIR), "kind": "home"},
            {"id": "apps", "label": t("appfiles.rootApps"), "path": str(APPS_DIR), "kind": "apps"},
            {"id": "configs", "label": t("appfiles.rootConfigs"), "path": str(CONFIGS_DIR), "kind": "configs"},
        ]
        for index, extra in enumerate(self.cfg.get("monitor.extra_roots", []) or []):
            path = Path(str(extra)).expanduser()
            if path.is_dir():
                items.append({"id": f"extra{index}",
                              "label": t("appfiles.rootExtra", name=path.name),
                              "path": str(path), "kind": "extra"})
        return items

    def _allowed_roots(self) -> List[Path]:
        roots = [HOME_DIR.resolve()]
        for extra in self.cfg.get("monitor.extra_roots", []) or []:
            path = Path(str(extra)).expanduser()
            if path.is_dir():
                roots.append(path.resolve())
        return roots

    def resolve(self, path: Union[str, Path], must_exist: bool = False) -> Path:
        """Приводит путь к абсолютному и проверяет, что он внутри разрешённых корней."""
        raw = Path(str(path)).expanduser()
        if not raw.is_absolute():
            raw = HOME_DIR / raw
        resolved = raw.resolve()
        allowed = self._allowed_roots()
        if not any(resolved == root or is_within(resolved, root) for root in allowed):
            raise PermissionError(t("appfiles.outsideRoots", path=resolved))
        if must_exist and not resolved.exists():
            raise FileNotFoundError(t("appfiles.notFound", path=resolved))
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(HOME_DIR))
        except ValueError:
            return str(path)

    # ------------------------------------------------------------------ #
    def tree(self, root: str = "", depth: int = 3, limit: int = 2000) -> Dict[str, Any]:
        base = self.resolve(root) if root else HOME_DIR
        if not base.is_dir():
            return {"ok": False, "error": t("appfiles.notDirectory", path=base), "nodes": []}
        count = 0
        skipped = 0

        def walk(directory: Path, level: int) -> List[FileNode]:
            nonlocal count, skipped
            nodes: List[FileNode] = []
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                return nodes
            for entry in entries:
                if count >= limit:
                    skipped += 1
                    continue
                name = entry.name
                if name in ("__pycache__", ".git") or name.startswith(".tmp-megatool"):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                count += 1
                if entry.is_dir():
                    node = FileNode(name=name, path=self.relative(entry), type="dir", mtime=stat.st_mtime)
                    if level + 1 < depth:
                        node.children = walk(entry, level + 1)
                    nodes.append(node)
                else:
                    nodes.append(FileNode(name=name, path=self.relative(entry), type="file",
                                          size=stat.st_size, mtime=stat.st_mtime))
            return nodes

        root_node = FileNode(name=base.name or str(base), path=self.relative(base), type="dir",
                             mtime=base.stat().st_mtime)
        root_node.children = walk(base, 0)
        return {"ok": True, "root": self.relative(base), "nodes": [n.to_dict() for n in root_node.children],
                "count": count, "truncated": skipped > 0, "limit": limit}

    def read(self, path: str, max_bytes: int = MAX_READ) -> Dict[str, Any]:
        target = self.resolve(path, must_exist=True)
        if target.is_dir():
            return {"ok": False, "error": t("appfiles.isDirectory")}
        stat = target.stat()
        if stat.st_size > max_bytes * 8 and target.suffix.lower() not in TEXT_EXTENSIONS:
            return {"ok": False, "error": t("appfiles.fileTooLarge", size=human_bytes(stat.st_size))}
        data = target.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": self.relative(target),
            "absolute": str(target),
            "text": text,
            "size": stat.st_size,
            "size_human": human_bytes(stat.st_size),
            "mtime": stat.st_mtime,
            "mtime_iso": iso(stat.st_mtime),
            "truncated": stat.st_size > len(data),
            "lines": text.count("\n") + 1,
            "readonly": not os.access(target, os.W_OK),
            "kind": self.detect_kind(target.name),
        }

    def write(self, path: str, content: str, backup: bool = True) -> Dict[str, Any]:
        target = self.resolve(path)
        if target.exists() and target.is_dir():
            return {"ok": False, "error": t("appfiles.isDirectory")}
        if target.exists() and not os.access(target, os.W_OK):
            return {"ok": False, "error": t("appfiles.noWriteAccess")}
        created = not target.exists()
        backup_path = ""
        if backup and target.exists() and target.stat().st_size > 0:
            candidate = target.with_suffix(target.suffix + ".bak")
            try:
                shutil.copy2(target, candidate)
                backup_path = self.relative(candidate)
            except OSError:
                backup_path = ""
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, content)
        stat = target.stat()
        return {
            "ok": True, "path": self.relative(target), "created": created,
            "size": stat.st_size, "mtime_iso": iso(stat.st_mtime), "backup": backup_path,
            "validation": validate(target.name, content),
        }

    def mkdir(self, path: str) -> Dict[str, Any]:
        target = self.resolve(path)
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": self.relative(target)}

    def rename(self, old: str, new: str) -> Dict[str, Any]:
        source = self.resolve(old, must_exist=True)
        destination = self.resolve(new)
        if destination.exists():
            return {"ok": False, "error": t("appfiles.targetExists")}
        source.rename(destination)
        return {"ok": True, "path": self.relative(destination)}

    def delete(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        target = self.resolve(path, must_exist=True)
        if target == HOME_DIR:
            return {"ok": False, "error": t("appfiles.cannotDeleteRoot")}
        if target.is_dir():
            if not recursive:
                try:
                    target.rmdir()
                except OSError:
                    return {"ok": False, "error": t("appfiles.directoryNotEmpty")}
            else:
                shutil.rmtree(target, ignore_errors=False)
        else:
            if not self.cfg.get("safety.allow_file_delete", True):
                return {"ok": False, "error": t("appfiles.deleteDisabled")}
            target.unlink()
        return {"ok": True, "path": self.relative(target)}

    def upload(self, path: str, filename: str, data: bytes) -> Dict[str, Any]:
        directory = self.resolve(path)
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w.\-]+", "_", filename)[:120] or t("appfiles.uploadFallbackName")
        target = directory / safe_name
        counter = 1
        while target.exists():
            target = directory / f"{Path(safe_name).stem}-{counter}{Path(safe_name).suffix}"
            counter += 1
        target.write_bytes(data)
        return {"ok": True, "path": self.relative(target), "size": len(data)}

    # ------------------------------------------------------------------ #
    def search(self, query: str, root: str = "", limit: int = 80) -> Dict[str, Any]:
        query = (query or "").strip()
        if len(query) < 2:
            return {"ok": False, "error": t("appfiles.queryTooShort"), "matches": []}
        base = self.resolve(root) if root else HOME_DIR
        matches: List[Dict[str, Any]] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".git", "data", "node_modules")]
            for name in filenames:
                if len(matches) >= limit:
                    break
                path = Path(dirpath) / name
                if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in ("Dockerfile", "Makefile"):
                    continue
                try:
                    if path.stat().st_size > MAX_READ:
                        continue
                    scanned += 1
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for number, line in enumerate(text.splitlines(), start=1):
                    if query.lower() in line.lower():
                        matches.append({
                            "path": self.relative(path),
                            "line": number,
                            "text": line.strip()[:240],
                        })
                        if len(matches) >= limit:
                            break
        return {"ok": True, "matches": matches, "scanned": scanned}

    # ------------------------------------------------------------------ #
    @staticmethod
    def detect_kind(filename: str) -> str:
        name = filename.lower()
        if name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            return "compose"
        if name.endswith((".yml", ".yaml")):
            return "yaml"
        if name.endswith(".json"):
            return "json"
        if name.endswith(".conf") and ("nginx" in name or name.endswith("site.conf")):
            return "nginx"
        if name.endswith((".service", ".socket", ".timer")):
            return "systemd"
        if name.endswith(".env") or name in (".env", ".env.example"):
            return "env"
        if name.startswith("Dockerfile"):
            return "dockerfile"
        if name.endswith(".ini"):
            return "ini"
        if name.endswith(".md"):
            return "markdown"
        if name.endswith((".sql",)):
            return "sql"
        return "text"

    def usage(self) -> Dict[str, Any]:
        used = 0
        files = 0
        for dirpath, _dirnames, filenames in os.walk(HOME_DIR):
            for name in filenames:
                try:
                    used += (Path(dirpath) / name).stat().st_size
                    files += 1
                except OSError:
                    continue
        return {"path": str(HOME_DIR), "files": files, "size": used, "size_human": human_bytes(used)}


# --------------------------------------------------------------------------- #
#  Проверка синтаксиса
# --------------------------------------------------------------------------- #

def validate(filename: str, content: str) -> Dict[str, Any]:
    """Проверяет содержимое по типу файла. Возвращает {ok, errors, warnings, kind}."""
    kind = Workspace.detect_kind(filename)
    errors: List[str] = []
    warnings: List[str] = []
    info: Dict[str, Any] = {}

    try:
        if kind in ("yaml", "compose"):
            if yaml is None:
                warnings.append(t("appfiles.yamlSkipped"))
            else:
                docs = list(yaml.safe_load_all(content))
                info["documents"] = len([d for d in docs if d is not None])
                for index, doc in enumerate(docs, start=1):
                    if doc is None:
                        continue
                    if kind == "compose":
                        if not isinstance(doc, dict):
                            errors.append(t("appfiles.composeDocObject", index=index))
                            continue
                        services = doc.get("services")
                        if not isinstance(services, dict) or not services:
                            errors.append(t("appfiles.composeNoServices"))
                        else:
                            info["services"] = list(services.keys())
                            for name, service in services.items():
                                if not isinstance(service, dict):
                                    errors.append(t("appfiles.composeServiceObject", name=name))
                                    continue
                                if not service.get("image") and not service.get("build"):
                                    warnings.append(t("appfiles.composeServiceImage", name=name))
                    elif isinstance(doc, dict) and "apiVersion" in doc and "kind" in doc:
                        info.setdefault("k8s", []).append(f"{doc.get('kind')}/{doc.get('metadata', {}).get('name', '?')}")
        elif kind == "json":
            json.loads(content)
        elif kind == "ini":
            parser = configparser.ConfigParser()
            parser.read_string(content)
            info["sections"] = parser.sections()
        elif kind == "env":
            for number, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    errors.append(t("appfiles.envBadLine", line=number))
                elif " " in stripped.split("=", 1)[0]:
                    warnings.append(t("appfiles.envSpaceInKey", line=number))
        elif kind == "nginx":
            balance = content.count("{") - content.count("}")
            if balance != 0:
                errors.append(t("appfiles.nginxBraces", left=content.count("{"),
                                right=content.count("}")))
            if not re.search(r"\b(server|upstream|http|location|stream)\b", content):
                warnings.append(t("appfiles.nginxNoDirectives"))
            if re.search(r"\blisten\s+(\d+)\b", content):
                for port in re.findall(r"\blisten\s+(\d+)\b", content):
                    if not 1 <= int(port) <= 65535:
                        errors.append(t("appfiles.portOutOfRange", port=port))
        elif kind == "systemd":
            if "[Service]" not in content and "[Unit]" not in content:
                errors.append(t("appfiles.systemdNoSections"))
            if "ExecStart" not in content:
                warnings.append(t("appfiles.systemdNoExecStart"))
            for section in re.findall(r"^\[(\w+)\]", content, re.M):
                if section not in ("Unit", "Service", "Install", "Socket", "Timer", "Path", "Mount"):
                    warnings.append(t("appfiles.systemdUnknownSection", section=section))
        elif kind == "dockerfile":
            if not re.search(r"^\s*FROM\s+\S+", content, re.M | re.I):
                errors.append(t("appfiles.dockerfileNoFrom"))
            if re.search(r"^\s*RUN\s+.*apt-get\s+upgrade", content, re.M | re.I):
                warnings.append(t("appfiles.dockerfileAptUpgrade"))
        else:
            info["note"] = t("appfiles.validationNotSupported")
    except Exception as exc:  # noqa: BLE001 — сообщения парсеров полезнее типа исключения
        message = str(exc).replace("\n", " ")[:400]
        errors.append(f"{type(exc).__name__}: {message}")

    # Дополнительные эвристики для ansible-плейбуков
    if kind in ("yaml",) and re.search(r"^\s*-\s*hosts:", content, re.M):
        info["ansible"] = True
        if "become" not in content and re.search(r"apt:|yum:|package:", content):
            warnings.append(t("appfiles.ansibleBecome"))

    if "{{" in content and kind in ("nginx", "systemd"):
        warnings.append(t("appfiles.templateLeftovers"))

    return {"ok": not errors, "errors": errors, "warnings": warnings, "kind": kind, "info": info}


# --------------------------------------------------------------------------- #
#  Галерея шаблонов для редактора
# --------------------------------------------------------------------------- #

def _t(tid: str, name: str, filename: str, description: str, content: str, category: str = "configs") -> Dict[str, Any]:
    return {"id": tid, "name": name, "filename": filename, "description": description,
            "content": content, "category": category}


TEMPLATES: List[Dict[str, Any]] = [
    _t("nginx-site", "Nginx: сайт", "site.conf",
       "Виртуальный хост с проксированием и кэшем статики",
       """server {
    listen 80;
    server_name example.com www.example.com;
    root /var/www/example.com;
    index index.html;

    client_max_body_size 32m;
    gzip on;

    location /static/ {
        expires 30d;
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
"""),
    _t("compose-basic", "Compose: приложение + БД", "docker-compose.yml",
       "Типовой стек из двух сервисов с healthcheck",
       """services:
  app:
    image: ghcr.io/example/app:latest
    restart: unless-stopped
    env_file: .env
    ports:
      - "8080:8000"
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_USER: app
      POSTGRES_DB: app
    volumes:
      - ./data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 10s
      timeout: 5s
      retries: 5
"""),
    _t("systemd-unit", "Systemd: юнит сервиса", "myapp.service",
       "Юнит с авто-перезапуском и ограничениями безопасности",
       """[Unit]
Description=My Application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=app
Group=app
WorkingDirectory=/opt/myapp
EnvironmentFile=-/opt/myapp/.env
ExecStart=/opt/myapp/bin/start
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/myapp

[Install]
WantedBy=multi-user.target
"""),
    _t("haproxy", "HAProxy: балансировка", "haproxy.cfg",
       "frontend/backend с проверками здоровья",
       """global
    log stdout format raw local0

defaults
    log global
    mode http
    option httplog
    timeout connect 5s
    timeout client  30s
    timeout server  30s

frontend web_in
    bind *:80
    default_backend web_servers

backend web_servers
    balance leastconn
    option httpchk GET /healthz
    http-check expect status 200
    server web1 10.0.0.11:8000 check weight 1 fall 3 rise 2
    server web2 10.0.0.12:8000 check weight 1 fall 3 rise 2

listen stats
    bind *:8404
    stats enable
    stats uri /stats
"""),
    _t("k8s-deployment", "Kubernetes: Deployment", "deployment.yaml",
       "Деплой с пробами, лимитами и RollingUpdate",
       """apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: default
  labels:
    app: app
spec:
  replicas: 2
  revisionHistoryLimit: 5
  selector:
    matchLabels:
      app: app
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: ghcr.io/example/app:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: app-secret
          resources:
            requests: { cpu: 100m, memory: 128Mi }
            limits: { cpu: "1", memory: 1Gi }
          readinessProbe:
            httpGet: { path: /healthz, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /healthz, port: 8000 }
            initialDelaySeconds: 20
            periodSeconds: 20
          securityContext:
            runAsNonRoot: true
            allowPrivilegeEscalation: false
"""),
    _t("k8s-service-ingress", "Kubernetes: Service + Ingress", "service.yaml",
       "Service типа ClusterIP и Ingress с TLS",
       """apiVersion: v1
kind: Service
metadata:
  name: app
spec:
  type: ClusterIP
  selector:
    app: app
  ports:
    - name: http
      port: 80
      targetPort: 8000
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
spec:
  ingressClassName: nginx
  tls:
    - hosts: [app.example.com]
      secretName: app-tls
  rules:
    - host: app.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: app
                port:
                  number: 80
"""),
    _t("ansible-playbook", "Ansible: плейбук", "playbook.yml",
       "Плейбук с ролями, тегами и проверкой",
       """---
- name: Развернуть приложение
  hosts: web
  become: true
  gather_facts: true
  vars:
    app_dir: /opt/app
    app_version: "1.0.0"

  pre_tasks:
    - name: Проверить свободное место
      ansible.builtin.assert:
        that:
          - (ansible_mounts | selectattr('mount', 'equalto', '/') | list | first).size_available > 2147483648
        fail_msg: "Меньше 2 ГБ свободного места на /"

  roles:
    - common
    - app

  tasks:
    - name: Дождаться ответа приложения
      ansible.builtin.uri:
        url: "http://{{ ansible_host }}:8000/healthz"
        status_code: 200
      retries: 10
      delay: 3
      register: health
      until: health.status == 200
      tags: [verify]
"""),
    _t("ansible-inventory", "Ansible: инвентарь", "inventory.ini",
       "Группы, переменные и вложенность",
       """[all:vars]
ansible_python_interpreter=/usr/bin/python3
ansible_user=deploy

[web]
web1 ansible_host=10.0.0.11
web2 ansible_host=10.0.0.12

[db]
db1 ansible_host=10.0.0.21

[prod:children]
web
db

[prod:vars]
env=production
"""),
    _t("prometheus-scrape", "Prometheus: сбор метрик", "prometheus.yml",
       "Сбор с хостов и правил алертов",
       """global:
  scrape_interval: 15s
  evaluation_interval: 30s

scrape_configs:
  - job_name: node
    static_configs:
      - targets:
          - "10.0.0.10:9100"
          - "10.0.0.11:9100"

  - job_name: containers
    static_configs:
      - targets: ["cadvisor:8080"]
"""),
    _t("env-file", "Переменные окружения", ".env",
       "Шаблон .env для compose-стека",
       """# Скопируйте в .env и заполните значения
APP_ENV=production
APP_PORT=8080
LOG_LEVEL=info

POSTGRES_USER=app
POSTGRES_DB=app
POSTGRES_PASSWORD=change-me

REDIS_PASSWORD=change-me
"""),
    _t("logrotate", "Logrotate", "app-logrotate",
       "Ежедневная ротация с сжатием",
       """/var/log/app/*.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    create 0640 root adm
}
"""),
    _t("dockerfile", "Dockerfile: Python", "Dockerfile",
       "Многослойная сборка с кэшем зависимостей и непривилегированным пользователем",
       """FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -r -u 10001 -m appuser \\
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["python", "-m", "app"]
"""),
    _t("k8s-secret", "Kubernetes: Secret + ConfigMap", "secret.yaml",
       "Секреты и конфигурация отдельно от образа",
       """apiVersion: v1
kind: Secret
metadata:
  name: app-secret
type: Opaque
stringData:
  DATABASE_URL: postgresql://app:change-me@db:5432/app
  SECRET_KEY: change-me
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  APP_ENV: production
  LOG_LEVEL: info
"""),
]


#: Ключи переводов названий и описаний шаблонов (содержимое файлов не переводится).
_TEMPLATE_KEYS: Dict[str, Tuple[str, str]] = {
    "nginx-site": ("appfiles.tplNginxSiteName", "appfiles.tplNginxSiteDescription"),
    "compose-basic": ("appfiles.tplComposeBasicName", "appfiles.tplComposeBasicDescription"),
    "systemd-unit": ("appfiles.tplSystemdUnitName", "appfiles.tplSystemdUnitDescription"),
    "haproxy": ("appfiles.tplHaproxyName", "appfiles.tplHaproxyDescription"),
    "k8s-deployment": ("appfiles.tplK8sDeploymentName", "appfiles.tplK8sDeploymentDescription"),
    "k8s-service-ingress": ("appfiles.tplK8sServiceIngressName",
                            "appfiles.tplK8sServiceIngressDescription"),
    "ansible-playbook": ("appfiles.tplAnsiblePlaybookName", "appfiles.tplAnsiblePlaybookDescription"),
    "ansible-inventory": ("appfiles.tplAnsibleInventoryName",
                          "appfiles.tplAnsibleInventoryDescription"),
    "prometheus-scrape": ("appfiles.tplPrometheusScrapeName",
                          "appfiles.tplPrometheusScrapeDescription"),
    "env-file": ("appfiles.tplEnvFileName", "appfiles.tplEnvFileDescription"),
    "logrotate": ("appfiles.tplLogrotateName", "appfiles.tplLogrotateDescription"),
    "dockerfile": ("appfiles.tplDockerfileName", "appfiles.tplDockerfileDescription"),
    "k8s-secret": ("appfiles.tplK8sSecretName", "appfiles.tplK8sSecretDescription"),
}


def _localized_template(item: Dict[str, Any]) -> Dict[str, Any]:
    """Копия шаблона с названием и описанием на языке интерфейса."""
    keys = _TEMPLATE_KEYS.get(str(item.get("id", "")))
    if not keys:
        return item
    return {**item, "name": t(keys[0]), "description": t(keys[1])}


def list_templates() -> List[Dict[str, Any]]:
    return [{"id": item["id"], "name": item["name"], "filename": item["filename"],
             "description": item["description"], "category": item["category"],
             "size": len(item["content"])} for item in map(_localized_template, TEMPLATES)]


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    for item in TEMPLATES:
        if item["id"] == template_id:
            return _localized_template(item)
    return None


def nginx_test(binary: str, path: Path) -> Dict[str, Any]:
    """Проверка конфига nginx реальным бинарником (если он доступен)."""
    from .util import run

    if not binary:
        return {"ok": False, "error": t("appfiles.nginxNotFound")}
    res = run([binary, "-t", "-c", str(path)], timeout=20)
    text = (res.out or "") + (res.err or "")
    return {"ok": res.ok, "text": text.strip(), "error": "" if res.ok else (res.error or text.strip())}
