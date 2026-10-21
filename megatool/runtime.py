"""Сборка и хранение служб приложения (единая точка входа для API).

Runtime создаётся один раз при старте: настройки, коллектор метрик, менеджер
задач, движки контейнеров, балансировщики, каталог приложений, Ansible,
Kubernetes и рабочее пространство. API-слой обращается к нему через app.state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .ansible import Ansible
from .appfiles import Workspace
from .balancer import BalancerManager
from .catalog import Catalog
from .changes import configure_audit
from .checks import ChecksManager
from .collector import Collector
from .access import AccessManager
from .config import Config
from .containers import ContainerRegistry
from .hosts import HostsManager
from .i18n import set_language
from .jobs import JobManager
from .k8s import CachedK8s, K8s
from .security import AuthManager
from .util import ensure_dir, which

from .config import CONFIGS_DIR, RUNS_DIR


@dataclass
class Runtime:
    cfg: Config
    auth: AuthManager
    access: AccessManager
    collector: Collector
    checks: ChecksManager
    hosts: HostsManager
    jobs: JobManager
    registry: ContainerRegistry
    balancers: BalancerManager
    k8s: K8s
    k8s_cached: CachedK8s
    ansible: Ansible
    catalog: Catalog
    workspace: Workspace
    started_at: float = field(default_factory=time.time)
    version: str = __version__
    notes: list[Any] = field(default_factory=list)

    def state(self) -> dict[str, Any]:
        """Сводное состояние для верхней панели интерфейса."""
        engines = self.registry.summary()
        k8s_status = self.k8s.available()
        ansible = self.ansible.status()
        return {
            "app": {"name": "MegaTool", "version": self.version,
                    "uptime": time.time() - self.started_at},
            "monitor": self.collector.public_state(),
            "engines": engines,
            "k8s": {"available": k8s_status.get("available", False),
                    "version": k8s_status.get("version", ""),
                    "context": self.k8s.context, "namespace": self.k8s.namespace},
            "ansible": {"available": ansible.get("available", False),
                        "version": ansible.get("version", ""),
                        "playbooks": len(ansible.get("playbooks", []))},
            "balancers": self.balancers.public_state(),
            "catalog": self.catalog.stats(),
            "jobs": {"running": self.jobs.active_count(), "recent": len(self.jobs.list(limit=100))},
            "checks": self.checks.summary(),
            "hosts": self.hosts.public_state(),
            "alerts": self.collector.active_alerts(),
            "auth": self.auth.public_state(),
            "access": self.access.summary(),
            "notes": self.notes,
        }


def _number_setting(value: Any, default: float, low: float, high: float) -> float:
    """Безопасно приводит число из настроек к допустимым границам.

    Конфиг можно править руками, поэтому кривое значение не должно ронять
    приложение на старте: берём значение по умолчанию.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(high, max(low, number))


def build_runtime(cfg: Config | None = None) -> Runtime:
    cfg = cfg or Config()
    set_language(cfg.get("ui.language", "ru"))
    ensure_dir(RUNS_DIR)
    ensure_dir(CONFIGS_DIR)
    # Ротация журнала действий: значения из настроек (audit.max_mb / audit.keep).
    configure_audit(
        _number_setting(cfg.get("audit.max_mb"), 5.0, 1.0, 1024.0),
        int(_number_setting(cfg.get("audit.keep"), 3, 1, 50)),
    )

    auth = AuthManager(cfg)
    access = AccessManager(cfg)
    jobs = JobManager(
        RUNS_DIR,
        keep=int(_number_setting(cfg.get("jobs.keep"), 60, 10, 500)),
        max_active=int(_number_setting(cfg.get("jobs.max_active"), 8, 1, 64)),
    )
    registry = ContainerRegistry(cfg)
    balancers = BalancerManager(cfg)
    collector = Collector(cfg)
    checks = ChecksManager(cfg)
    hosts = HostsManager(cfg)
    k8s = K8s(cfg)
    ansible = Ansible(cfg)
    ansible.ensure_scaffold()
    catalog = Catalog(cfg, registry, jobs)
    workspace = Workspace(cfg)

    # Провайдеры алертов: контейнеры, балансировщик, проверки доступности
    # и инвентарь серверов.
    collector.alert_providers["containers"] = registry.container_alerts
    collector.alert_providers["balancers"] = balancers.alerts
    collector.alert_providers["checks"] = checks.alerts
    collector.alert_providers["hosts"] = hosts.alerts

    notes: list[Any] = []
    if not collector.state.get("psutil"):
        notes.append({"key": "notes.noPsutil"})
    if not registry.summary().get("available_any"):
        notes.append({"key": "notes.noContainers"})
    if not ansible.status().get("available"):
        notes.append({"key": "notes.noAnsible"})
    if not k8s.available().get("available"):
        notes.append({"key": "notes.noKubectl"})
    ssh_override = str(cfg.get("engines.ssh", "") or "")
    if not which("ssh", ssh_override or None):
        notes.append({"key": "notes.noSsh"})

    runtime = Runtime(
        cfg=cfg,
        auth=auth,
        access=access,
        collector=collector,
        checks=checks,
        hosts=hosts,
        jobs=jobs,
        registry=registry,
        balancers=balancers,
        k8s=k8s,
        k8s_cached=CachedK8s(k8s),
        ansible=ansible,
        catalog=catalog,
        workspace=workspace,
        notes=notes,
    )
    return runtime
