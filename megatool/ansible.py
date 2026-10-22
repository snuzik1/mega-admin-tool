"""Ansible: инвентарь, плейбуки, запуск и живой вывод.

Плейбуки и инвентарь лежат в библиотеке конфигов MegaTool
(`~/.megatool/configs/ansible/`), что позволяет править их во встроенном
редакторе. Запуск идёт через ansible-playbook в отдельной задаче (см. jobs.py),
поэтому длинные прогоны не блокируют интерфейс и их видно в реальном времени.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config import CONFIGS_DIR, Config
from .i18n import get_language, t
from .jobs import Job, JobManager
from .util import CmdResult, ensure_dir, is_safe_limit, is_within, iso, run, which

ANSIBLE_DIR = CONFIGS_DIR / "ansible"
PLAYBOOKS_DIR = ANSIBLE_DIR / "playbooks"
INVENTORY_PATH = ANSIBLE_DIR / "inventory.ini"

DEFAULT_INVENTORY = """# Инвентарь MegaTool. Формат INI, поддерживается и YAML-инвентарь.
# Группы можно вкладывать, переменные задавать на уровне хоста или группы.

[local]
localhost ansible_connection=local

[web]
# web1 ansible_host=10.0.0.11 ansible_user=deploy
# web2 ansible_host=10.0.0.12 ansible_user=deploy

[db]
# db1 ansible_host=10.0.0.21 ansible_user=deploy

[prod:children]
web
db

[prod:vars]
ansible_python_interpreter=/usr/bin/python3
"""

DEFAULT_PLAYBOOK = """---
# Пример плейбука MegaTool. Отредактируйте под свою инфраструктуру.
- name: Базовая проверка хостов
  hosts: all
  gather_facts: true
  become: false
  tasks:
    - name: Показать версию ОС
      ansible.builtin.debug:
        msg: "{{ ansible_distribution }} {{ ansible_distribution_version }} ({{ ansible_hostname }})"

    - name: Проверить свободное место на /
      ansible.builtin.assert:
        that:
          - (ansible_mounts | selectattr('mount', 'equalto', '/') | list | first).size_available > 1073741824
        fail_msg: "На / осталось меньше 1 ГБ"
        success_msg: "Место на / в порядке"
"""

_DEFAULT_INVENTORY_EN = """# MegaTool inventory. INI format; YAML inventories are supported too.
# Groups can be nested; variables can be set at host or group level.

[local]
localhost ansible_connection=local

[web]
# web1 ansible_host=10.0.0.11 ansible_user=deploy
# web2 ansible_host=10.0.0.12 ansible_user=deploy

[db]
# db1 ansible_host=10.0.0.21 ansible_user=deploy

[prod:children]
web
db

[prod:vars]
ansible_python_interpreter=/usr/bin/python3
"""

_DEFAULT_PLAYBOOK_EN = """---
# MegaTool sample playbook. Edit it for your infrastructure.
- name: Basic host check
  hosts: all
  gather_facts: true
  become: false
  tasks:
    - name: Show OS version
      ansible.builtin.debug:
        msg: "{{ ansible_distribution }} {{ ansible_distribution_version }} ({{ ansible_hostname }})"

    - name: Check free space on /
      ansible.builtin.assert:
        that:
          - (ansible_mounts | selectattr('mount', 'equalto', '/') | list | first).size_available > 1073741824
        fail_msg: "Less than 1 GB left on /"
        success_msg: "Disk space on / is fine"
"""


def _scaffold_inventory() -> str:
    """Стартовый инвентарь на языке интерфейса."""
    return _DEFAULT_INVENTORY_EN if get_language() == "en" else DEFAULT_INVENTORY


def _scaffold_playbook() -> str:
    """Стартовый плейбук на языке интерфейса."""
    return _DEFAULT_PLAYBOOK_EN if get_language() == "en" else DEFAULT_PLAYBOOK


_TAG_RE = re.compile(r"^[A-Za-z0-9_,\-.]{1,128}$")


class Ansible:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._cache: Dict[str, Any] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    @property
    def playbook_bin(self) -> Optional[str]:
        return which("ansible-playbook", self.cfg.get("engines.ansible_playbook", "") or None)

    @property
    def ansible_bin(self) -> Optional[str]:
        return which("ansible", self.cfg.get("engines.ansible", "") or None)

    @property
    def inventory_bin(self) -> Optional[str]:
        return which("ansible-inventory", self.cfg.get("engines.ansible_inventory", "") or None)

    def inventory_path(self) -> Path:
        custom = str(self.cfg.get("ansible.inventory", "") or "")
        if custom:
            return Path(custom).expanduser()
        return INVENTORY_PATH

    def playbooks_dir(self) -> Path:
        custom = str(self.cfg.get("ansible.playbooks_dir", "") or "")
        return Path(custom).expanduser() if custom else PLAYBOOKS_DIR

    def ensure_scaffold(self) -> None:
        """Создаёт пример инвентаря и плейбука, если библиотека пуста."""
        ensure_dir(self.playbooks_dir())
        inventory = self.inventory_path()
        if not inventory.is_file():
            ensure_dir(inventory.parent)
            inventory.write_text(_scaffold_inventory(), encoding="utf-8")
        site = self.playbooks_dir() / "site.yml"
        if not site.is_file():
            site.write_text(_scaffold_playbook(), encoding="utf-8")

    # ------------------------------------------------------------------ #
    def status(self, refresh: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self._cache.get("status") and not refresh \
                    and time.time() - self._cache.get("status_ts", 0) < 120:
                return self._cache["status"]
            playbook = self.playbook_bin
            ansible = self.ansible_bin
            version = ""
            if playbook:
                res = run([playbook, "--version"], timeout=25)
                first = (res.out or "").splitlines()
                if first:
                    version = first[0].strip()
            status = {
                "available": bool(playbook or ansible),
                "playbook_bin": playbook,
                "ansible_bin": ansible,
                "inventory_bin": self.inventory_bin,
                "version": version,
                "inventory": str(self.inventory_path()),
                "playbooks_dir": str(self.playbooks_dir()),
                "inventory_exists": self.inventory_path().is_file(),
                "playbooks": [p["name"] for p in self.playbooks()],
                "error": "" if (playbook or ansible) else
                t("ansible.notFound"),
            }
            self._cache["status"] = status
            self._cache["status_ts"] = time.time()
            return status

    # ------------------------------------------------------------------ #
    #  Инвентарь
    # ------------------------------------------------------------------ #
    def inventory_tree(self) -> Dict[str, Any]:
        inventory = self.inventory_path()
        if not inventory.is_file():
            return {"ok": False, "error": t("ansible.inventoryNotFound"), "groups": [], "hosts": []}
        if self.inventory_bin:
            res = run([self.inventory_bin, "-i", str(inventory), "--list"], timeout=30)
            if res.ok and res.out.strip():
                import json as _json

                try:
                    data = _json.loads(res.out)
                except _json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    return self._tree_from_json(data, source="ansible-inventory --list")
        text = ""
        try:
            text = inventory.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "error": str(exc), "groups": [], "hosts": []}
        return self._tree_from_ini(text)

    @staticmethod
    def _tree_from_json(data: Dict[str, Any], source: str) -> Dict[str, Any]:
        meta = data.pop("_meta", {}) if isinstance(data, dict) else {}
        hostvars = (meta or {}).get("hostvars", {}) if isinstance(meta, dict) else {}
        groups = []
        all_hosts: Set[str] = set()
        for name, body in (data or {}).items():
            if not isinstance(body, dict):
                continue
            hosts = body.get("hosts") or []
            children = body.get("children") or []
            variables = body.get("vars") or {}
            groups.append({
                "name": name,
                "hosts": [{"name": h, "vars": hostvars.get(h, {})} for h in hosts],
                "children": children,
                "vars": variables,
            })
            all_hosts.update(hosts)
        groups.sort(key=lambda g: (g["name"] != "all", g["name"]))
        return {"ok": True, "source": source, "groups": groups,
                "hosts": sorted(all_hosts), "count": len(all_hosts)}

    @staticmethod
    def _tree_from_ini(text: str) -> Dict[str, Any]:
        """Простой разбор INI-инвентаря (используется, если нет ansible-inventory)."""
        groups: Dict[str, Dict[str, Any]] = {}
        current: Optional[str] = None
        ungrouped: List[Dict[str, Any]] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                name = line[1:-1].strip()
                if ":" in name:
                    group, _, suffix = name.partition(":")
                    name = group.strip()
                    as_vars = suffix.strip() == "vars"
                    as_children = suffix.strip() == "children"
                else:
                    as_vars = as_children = False
                entry = groups.setdefault(name, {"name": name, "hosts": [], "children": [], "vars": {}})
                current = name
                if as_vars or as_children:
                    current = name
                    entry.setdefault("_mode", "vars" if as_vars else "children")
                else:
                    entry["_mode"] = "hosts"
                continue
            if current is None:
                host, _, vars_text = line.partition(" ")
                if host:
                    ungrouped.append({"name": host, "vars": _parse_kv(vars_text)})
                continue
            mode = groups[current].get("_mode", "hosts")
            if mode == "vars":
                key, _, value = line.partition("=")
                groups[current]["vars"][key.strip()] = value.strip()
            elif mode == "children":
                groups[current]["children"].append(line.strip())
            else:
                host, _, vars_text = line.partition(" ")
                groups[current]["hosts"].append({"name": host, "vars": _parse_kv(vars_text)})
        if ungrouped:
            groups.setdefault("ungrouped", {"name": "ungrouped", "hosts": [], "children": [],
                                            "vars": {}, "_mode": "hosts"})["hosts"].extend(ungrouped)
        ordered = []
        for name in sorted(groups, key=lambda n: (n != "all", n)):
            group = groups[name]
            group.pop("_mode", None)
            ordered.append(group)
        hosts = sorted({h["name"] for g in ordered for h in g["hosts"]})
        return {"ok": True, "source": t("ansible.inventorySourceIni"), "groups": ordered,
                "hosts": hosts, "count": len(hosts)}

    # ------------------------------------------------------------------ #
    #  Плейбуки
    # ------------------------------------------------------------------ #
    def playbooks(self) -> List[Dict[str, Any]]:
        directory = self.playbooks_dir()
        if not directory.is_dir():
            return []
        items = []
        for path in sorted(directory.rglob("*.y*ml")):
            if path.name.startswith("."):
                continue
            stat = path.stat()
            summary = self._playbook_summary(path)
            items.append({
                "name": path.name,
                "path": str(path),
                "rel": str(path.relative_to(directory)),
                "size": stat.st_size,
                "modified": iso(stat.st_mtime),
                **summary,
            })
        return items

    @staticmethod
    def _playbook_summary(path: Path) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"plays": [], "hosts": [], "task_count": 0, "roles": []}
        try:
            import yaml
        except ImportError:
            return summary
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8", errors="replace")))
        except Exception as exc:
            summary["parse_error"] = str(exc)[:200]
            return summary
        for doc in docs:
            if not isinstance(doc, list):
                continue
            for play in doc:
                if not isinstance(play, dict):
                    continue
                name = str(play.get("name", "") or "")
                hosts = str(play.get("hosts", "") or "")
                roles = play.get("roles") or []
                tasks = play.get("tasks") or []
                pre = play.get("pre_tasks") or []
                post = play.get("post_tasks") or []
                summary["plays"].append({"name": name, "hosts": hosts,
                                         "become": bool(play.get("become")),
                                         "tasks": len(tasks) + len(pre) + len(post)})
                if hosts:
                    summary["hosts"].append(hosts)
                summary["task_count"] += len(tasks) + len(pre) + len(post)
                for role in roles:
                    summary["roles"].append(role if isinstance(role, str) else str(role.get("role", "")))
        summary["hosts"] = sorted(set(summary["hosts"]))
        return summary

    def resolve_playbook(self, name_or_path: str) -> Optional[Path]:
        candidate = Path(name_or_path)
        directory = self.playbooks_dir()
        if candidate.is_absolute() and candidate.is_file():
            resolved = candidate.resolve()
            if is_within(resolved, directory):
                return resolved
            return None
        direct = directory / name_or_path
        if direct.is_file():
            return direct.resolve()
        for item in self.playbooks():
            if item["name"] == name_or_path or item["rel"] == name_or_path:
                return Path(item["path"])
        return None

    # ------------------------------------------------------------------ #
    #  Запуск
    # ------------------------------------------------------------------ #
    def run_playbook(
        self,
        jobs: JobManager,
        playbook: str,
        limit: str = "",
        tags: str = "",
        skip_tags: str = "",
        check: bool = False,
        diff: bool = False,
        extra_vars: Optional[Dict[str, str]] = None,
        verbosity: int = 0,
        inventory: str = "",
    ) -> Dict[str, Any]:
        binary = self.playbook_bin
        if not binary:
            return {"ok": False, "error": t("ansible.playbookBinNotFound")}
        path = self.resolve_playbook(playbook)
        if not path:
            return {"ok": False, "error": t("ansible.playbookNotFound", playbook=playbook)}
        if limit and not is_safe_limit(limit):
            return {"ok": False, "error": t("ansible.badLimit")}
        if tags and not _TAG_RE.match(tags):
            return {"ok": False, "error": t("ansible.badTags")}
        if skip_tags and not _TAG_RE.match(skip_tags):
            return {"ok": False, "error": t("ansible.badSkipTags")}

        inventory_path = Path(inventory).expanduser() if inventory else self.inventory_path()
        cmd = [binary, "-i", str(inventory_path), str(path)]
        if limit:
            cmd += ["--limit", limit.strip()]
        if tags:
            cmd += ["--tags", tags]
        if skip_tags:
            cmd += ["--skip-tags", skip_tags]
        if check:
            cmd += ["--check"]
        if diff:
            cmd += ["--diff"]
        if extra_vars:
            import json as _json

            cleaned = {str(k): str(v) for k, v in extra_vars.items() if str(k).strip()}
            if cleaned:
                cmd += ["-e", _json.dumps(cleaned, ensure_ascii=False)]
        if verbosity:
            cmd.append("-" + "v" * max(1, min(4, verbosity)))

        job = jobs.submit(
            title=t("ansible.jobTitle", name=path.name) + (t("ansible.jobLimit", limit=limit) if limit else ""),
            cmd=cmd,
            cwd=str(path.parent),
            kind="ansible",
            meta={"playbook": str(path), "limit": limit, "check": check, "tags": tags,
                  "extra_vars": list((extra_vars or {}).keys())},
        )
        return {"ok": True, "job": job.to_dict()}

    def adhoc(
        self,
        jobs: JobManager,
        module: str = "ping",
        pattern: str = "all",
        args: str = "",
        inventory: str = "",
    ) -> Dict[str, Any]:
        binary = self.ansible_bin
        if not binary:
            return {"ok": False, "error": t("ansible.binNotFound")}
        if not re.match(r"^[a-z_][a-z0-9_.]{1,64}$", module or ""):
            return {"ok": False, "error": t("ansible.badModule")}
        if not is_safe_limit(pattern):
            return {"ok": False, "error": t("ansible.badPattern")}
        inventory_path = Path(inventory).expanduser() if inventory else self.inventory_path()
        cmd = [binary, "-i", str(inventory_path), pattern.strip(), "-m", module]
        if args:
            cmd += ["-a", args[:500]]
        job = jobs.submit(title=t("ansible.adhocTitle", module=module, pattern=pattern), cmd=cmd,
                          cwd=str(inventory_path.parent), kind="ansible",
                          meta={"module": module, "pattern": pattern})
        return {"ok": True, "job": job.to_dict()}

    def syntax_check(self, playbook: str) -> Dict[str, Any]:
        binary = self.playbook_bin
        if not binary:
            return {"ok": False, "error": t("ansible.playbookBinMissing")}
        path = self.resolve_playbook(playbook)
        if not path:
            return {"ok": False, "error": t("ansible.playbookNotFound", playbook=playbook)}
        res: CmdResult = run([binary, "--syntax-check", "-i", str(self.inventory_path()), str(path)],
                             timeout=90, cwd=str(path.parent))
        return {"ok": res.ok, "text": (res.out or "") + (res.err or ""),
                "error": "" if res.ok else (res.err or res.error)[:400]}

    def write_inventory(self, content: str) -> Dict[str, Any]:
        path = self.inventory_path()
        ensure_dir(path.parent)
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, backup)
            except OSError:
                pass
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(path)}


def _parse_kv(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for token in (text or "").split():
        if "=" in token:
            key, _, value = token.partition("=")
            out[key.strip()] = value.strip()
    return out
