"""Каталог приложений: развёртывание рецептов, управление ими и учёт.

Развёрнутое приложение — это каталог в `~/.megatool/apps/<slug>` с готовыми
файлами (compose, манифесты, конфиги), сгенерированными паролями в `.env` и
файлом `meta.json` (что и когда развернули). Для compose-стека доступны
управление (up/down/restart/pull/logs) и живой статус сервисов.

Исходный код самих рецептов лежит в `recipes.py`, развёрнутые копии можно
править в редакторе конфигов — правки не теряются.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from .config import APPS_DIR, Config
from .containers import ContainerRegistry
from .i18n import t
from .jobs import JobManager
from .recipes import get_recipe, render
from .util import human_bytes, iso, rand_password, safe_id, slugify

META_NAME = "meta.json"
MOUNT_RE = re.compile(r"^\s*-\s*\./(?P<dir>[A-Za-z0-9_./-]+):", re.M)
SECRET_MARKER = "__secret__"


class Catalog:
    def __init__(self, cfg: Config, registry: ContainerRegistry, jobs: JobManager) -> None:
        self.cfg = cfg
        self.registry = registry
        self.jobs = jobs

    # ------------------------------------------------------------------ #
    #  Развёртывание
    # ------------------------------------------------------------------ #
    def deploy(
        self,
        recipe_id: str,
        name: str = "",
        values: dict[str, str] | None = None,
        start: bool = False,
        engine: str = "",
    ) -> dict[str, Any]:
        recipe = get_recipe(recipe_id)
        if not recipe:
            return {"ok": False, "error": t("catalog.recipeNotFound", recipe=recipe_id)}
        values = {str(k): str(v) for k, v in (values or {}).items()}

        # 1. Собираем итоговые значения: значения по умолчанию + ввод пользователя.
        final: dict[str, str] = {}
        secrets_created: list[str] = []
        for item in recipe.get("vars", []):
            value = values.get(item["key"], item.get("default", ""))
            if not value:
                return {"ok": False, "error": t("catalog.fieldRequired",
                                                 field=item.get("label", item["key"]))}
            final[item["key"]] = value
        for item in recipe.get("env", []):
            key = item["key"]
            value = values.get(key, "")
            if not value or value == SECRET_MARKER:
                value = rand_password() if (item.get("default") == SECRET_MARKER or item.get("secret")) else item.get("default", "")
                if item.get("default") == SECRET_MARKER:
                    secrets_created.append(key)
            final[key] = value

        # 2. Имя и уникальный каталог.
        app_name = final.get("NAME") or name or recipe["id"]
        safe_app_name = safe_id(app_name)
        if not safe_app_name:
            return {"ok": False, "error": t("catalog.badName")}
        if "{{PORT}}" in " ".join(recipe.get("ports", [])):
            port = str(final.get("PORT", "")).strip()
            if not port.isdigit():
                return {"ok": False, "error": t("catalog.portNotNumber")}
        slug = self._unique_slug(slugify(safe_app_name, recipe["id"]))
        target = APPS_DIR / slug
        target.mkdir(parents=True, exist_ok=True)

        # 3. Рендерим и записываем файлы.
        written: list[str] = []
        for rel_path, content in (recipe.get("files") or {}).items():
            rendered_path, missing_path = render(rel_path, final)
            rendered_text, missing = render(content, final)
            if missing_path or missing:
                return {"ok": False, "error": t("catalog.templateMissing", path=rel_path,
                                                 fields=missing + missing_path)}
            destination = target / rendered_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered_text, encoding="utf-8", newline="")
            if destination.suffix in (".env", ".conf", ".service") or destination.name.endswith((".env", ".ini")):
                try:
                    destination.chmod(0o600)
                except OSError:
                    pass
            written.append(rendered_path)

        # 4. Каталоги для bind-mount'ов (./data и подобные) + .gitignore.
        created_dirs: list[str] = []
        compose_files = [target / name for name in
                         ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
                         if (target / name).is_file()]
        for compose_file in compose_files:
            try:
                text = compose_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in MOUNT_RE.finditer(text):
                directory = (target / match.group("dir")).resolve()
                if target.resolve() in directory.parents or directory == target.resolve():
                    (target / match.group("dir")).mkdir(parents=True, exist_ok=True)
                    created_dirs.append(match.group("dir"))
        if compose_files:
            gitignore = target / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("data/\n*.env\n.env\n", encoding="utf-8")

        # 5. .env с секретами и переменными.
        env_values = {item["key"]: final.get(item["key"], "") for item in recipe.get("env", [])}
        if env_values:
            lines = [t("catalog.envHeader", date=iso()), t("catalog.envSecretsWarning"), ""]
            lines += [f"{key}={value}" for key, value in env_values.items()]
            env_path = target / ".env"
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            try:
                env_path.chmod(0o600)
            except OSError:
                pass
            written.append(".env")

        # 6. Метаданные и учёт.
        meta = {
            "slug": slug,
            "recipe": recipe["id"],
            "recipe_name": recipe["name"],
            "kind": recipe["kind"],
            "category": recipe["category"],
            "name": safe_app_name,
            "created": time.time(),
            "created_iso": iso(),
            "dir": str(target),
            "files": written,
            "dirs": sorted(set(created_dirs)),
            "ports": [render(item, final)[0] for item in recipe.get("ports", [])],
            "values": {key: ("•••" if key in secrets_created else value) for key, value in final.items()},
            "secrets_created": secrets_created,
            "engine": engine or (self.registry.preferred().kind if self.registry.preferred() else ""),
            "warning": recipe.get("warning", ""),
            "apply_hint": recipe.get("apply_hint", ""),
        }
        (target / META_NAME).write_text(_json(meta), encoding="utf-8")
        deployed = dict(self.cfg.get("catalog.deployed", {}) or {})
        deployed[slug] = meta
        self.cfg.set("catalog.deployed", deployed)
        self.cfg.save()

        result: dict[str, Any] = {
            "ok": True,
            "slug": slug,
            "dir": str(target),
            "files": sorted(written),
            "ports": meta["ports"],
            "secrets_created": secrets_created,
            "warning": recipe.get("warning", ""),
            "apply_hint": recipe.get("apply_hint", ""),
            "kind": recipe["kind"],
        }

        # 7. Запуск, если попросили и это применимо.
        if start and recipe["kind"] == "compose":
            started = self.action(slug, "up")
            result["start"] = started
        elif start and recipe["kind"] == "manifest":
            applied = self.apply_manifests(slug)
            result["apply"] = applied
        return result

    def _unique_slug(self, base: str) -> str:
        slug = base
        counter = 1
        while (APPS_DIR / slug).exists():
            counter += 1
            slug = f"{base}-{counter}"
        return slug

    # ------------------------------------------------------------------ #
    #  Список и статусы
    # ------------------------------------------------------------------ #
    def deployed(self, with_status: bool = True) -> list[dict[str, Any]]:
        deployed = dict(self.cfg.get("catalog.deployed", {}) or {})
        items: list[dict[str, Any]] = []
        engine = self.registry.preferred()
        for slug, meta in sorted(deployed.items(), key=lambda kv: -(kv[1].get("created") or 0)):
            entry = dict(meta)
            entry["slug"] = slug
            path = Path(meta.get("dir", ""))
            entry["exists"] = path.is_dir()
            entry["size"] = 0
            entry["files_count"] = 0
            if entry["exists"]:
                for file in path.rglob("*"):
                    try:
                        if file.is_file():
                            entry["size"] += file.stat().st_size
                            entry["files_count"] += 1
                    except OSError:
                        continue
            entry["size_human"] = human_bytes(entry["size"])
            entry["status"] = []
            entry["running"] = 0
            entry["total"] = 0
            entry["status_error"] = ""
            if with_status and meta.get("kind") == "compose" and engine and entry["exists"]:
                status = engine.compose_ps(path)
                entry["status"] = status.get("items", [])
                entry["running"] = len([s for s in entry["status"] if s.get("state") == "running"])
                entry["total"] = len(entry["status"])
                entry["status_error"] = status.get("error", "")
            items.append(entry)
        return items

    def app(self, slug: str) -> dict[str, Any] | None:
        deployed = dict(self.cfg.get("catalog.deployed", {}) or {})
        meta = deployed.get(safe_id(slug, ""))
        if not meta:
            return None
        meta = dict(meta)
        meta["slug"] = slug
        return meta

    def stats(self) -> dict[str, Any]:
        items = self.deployed(with_status=False)
        return {
            "total": len(items),
            "compose": len([i for i in items if i.get("kind") == "compose"]),
            "manifest": len([i for i in items if i.get("kind") == "manifest"]),
            "config": len([i for i in items if i.get("kind") == "config"]),
            "size": sum(i.get("size", 0) for i in items),
        }

    # ------------------------------------------------------------------ #
    #  Управление
    # ------------------------------------------------------------------ #
    def action(self, slug: str, action: str, service: str = "") -> dict[str, Any]:
        meta = self.app(slug)
        if not meta:
            return {"ok": False, "error": t("catalog.appNotFound")}
        path = Path(meta.get("dir", ""))
        if not path.is_dir():
            return {"ok": False, "error": t("catalog.dirMissing", path=path)}
        engine = self.registry.get(meta.get("engine") or None) or self.registry.preferred()
        if not engine:
            return {"ok": False, "error": t("catalog.noEngine")}
        ok, error = engine.require()
        if not ok:
            return {"ok": False, "error": error}

        if action == "ps":
            return {"ok": True, **engine.compose_ps(path)}
        if action not in {"up", "down", "restart", "stop", "start", "pull", "build", "logs"}:
            return {"ok": False, "error": t("catalog.unsupportedAction", action=action)}
        argv, error = engine.compose_command(path, action, service=service)
        if not argv:
            return {"ok": False, "error": error}
        job = self.jobs.submit(
            title=t("catalog.jobTitle", name=meta.get("recipe_name", slug),
                    action=t(f"catalog.actions.{action}")),
            cmd=argv, cwd=str(path), kind="compose",
            meta={"slug": slug, "action": action},
        )
        return {"ok": True, "job": job.to_dict()}

    def logs(self, slug: str, tail: int = 200, service: str = "") -> dict[str, Any]:
        meta = self.app(slug)
        if not meta:
            return {"ok": False, "error": t("catalog.appNotFound"), "text": ""}
        path = Path(meta.get("dir", ""))
        engine = self.registry.get(meta.get("engine") or None) or self.registry.preferred()
        if not engine:
            return {"ok": False, "error": t("catalog.engineUnavailable"), "text": ""}
        files = engine.compose_files_in(path)
        if not files:
            return {"ok": False, "error": t("catalog.composeFileNotFound"), "text": ""}
        result = engine.compose(path, "logs", service=service, files=files)
        text = (result.out or "") + (result.err or "")
        return {"ok": bool(text.strip()), "text": "\n".join(text.splitlines()[-max(1, tail):]),
                "error": "" if result.ok else (result.error or "")}

    def apply_manifests(self, slug: str, dry_run: bool = False) -> dict[str, Any]:
        """Применяет k8s-манифесты приложения (если доступен kubectl)."""
        meta = self.app(slug)
        if not meta:
            return {"ok": False, "error": t("catalog.appNotFound")}
        if meta.get("kind") != "manifest":
            return {"ok": False, "error": t("catalog.notManifest")}
        from .k8s import K8s

        k8s = K8s(self.cfg)
        if not k8s.available().get("available"):
            return {"ok": False, "error": t("catalog.kubectlNotFound"),
                    "hint": meta.get("apply_hint", t("catalog.applyHint"))}
        path = Path(meta.get("dir", ""))
        results = []
        for file in sorted(path.glob("*.y*ml")):
            results.append({"file": file.name, **k8s.apply_file(file, dry_run=dry_run)})
        return {"ok": all(item.get("ok") for item in results), "results": results, "dir": str(path)}

    def k8s_delete(self, slug: str) -> dict[str, Any]:
        meta = self.app(slug)
        if not meta or meta.get("kind") != "manifest":
            return {"ok": False, "error": t("catalog.appNotFound")}
        from .k8s import K8s

        k8s = K8s(self.cfg)
        path = Path(meta.get("dir", ""))
        results = [{"file": file.name, **k8s.delete_file(file)} for file in sorted(path.glob("*.y*ml"))]
        return {"ok": all(item.get("ok") for item in results), "results": results}

    def remove(self, slug: str, delete_files: bool = True, stop_first: bool = True) -> dict[str, Any]:
        meta = self.app(slug)
        if not meta:
            return {"ok": False, "error": t("catalog.appNotFound")}
        path = Path(meta.get("dir", ""))
        stopped = ""
        if stop_first and meta.get("kind") == "compose" and path.is_dir():
            engine = self.registry.get(meta.get("engine") or None) or self.registry.preferred()
            if engine and engine.require()[0]:
                result = engine.compose(path, "down")
                stopped = t("catalog.stopped") if result.ok else (
                    result.error or t("catalog.stopFailed"))
        if delete_files and path.is_dir():
            if not str(path).startswith(str(APPS_DIR)):
                return {"ok": False, "error": t("catalog.deleteOutside")}
            shutil.rmtree(path, ignore_errors=False)
        deployed = dict(self.cfg.get("catalog.deployed", {}) or {})
        deployed.pop(slug, None)
        self.cfg.set("catalog.deployed", deployed)
        self.cfg.save()
        return {"ok": True, "stopped": stopped, "deleted": delete_files}


def _json(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)
