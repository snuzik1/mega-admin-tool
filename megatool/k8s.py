"""Kubernetes через kubectl: обзор кластера, узлы, поды, события, действия.

MegaTool не переключает глобальный context у kubectl — выбранные context и
namespace просто подставляются флагом к каждому вызову, поэтому пользовательская
конфигурация не меняется.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import HOME_DIR, Config
from .i18n import t
from .util import CmdResult, human_bytes, is_within, iso, json_any, run, safe_id, which

_KIND_ALLOWED = {
    "pod", "pods", "deployment", "deployments", "statefulset", "statefulsets",
    "daemonset", "daemonsets", "service", "services", "ingress", "ingresses",
    "configmap", "configmaps", "secret", "secrets", "job", "jobs", "cronjob",
    "cronjobs", "namespace", "namespaces", "persistentvolumeclaim",
    "persistentvolumeclaims", "persistentvolume", "persistentvolumes",
}


def parse_cpu(value: Any) -> float:
    """«250m» -> 0.25 ядра, «2» -> 2.0."""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        if text.endswith("m"):
            return round(float(text[:-1]) / 1000.0, 3)
        if text.endswith("n"):
            return round(float(text[:-1]) / 1e9, 3)
        if text.endswith("u"):
            return round(float(text[:-1]) / 1e6, 3)
        return round(float(text), 3)
    except ValueError:
        return 0.0


def parse_qty(value: Any) -> float:
    """«8123456Ki», «2Gi», «1024M» -> байты."""
    if value is None:
        return 0.0
    text = str(value).strip()
    factors = {
        "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4, "Pi": 1024 ** 5,
        "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4, "P": 1000 ** 5,
        "k": 1000,
    }
    for suffix, factor in sorted(factors.items(), key=lambda kv: -len(kv[0])):
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


class K8s:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    @property
    def binary(self) -> Optional[str]:
        return which("kubectl", self.cfg.get("engines.kubectl", "") or None)

    def available(self) -> Dict[str, Any]:
        binary = self.binary
        if not binary:
            return {"available": False, "version": "", "error": t("k8s.kubectlMissing")}
        res = run([binary, "version", "--client", "--output", "json"], timeout=20)
        version = ""
        data = json_any(res.out)
        if isinstance(data, dict):
            version = str(data.get("clientVersion", {}).get("gitVersion", ""))
        if not version:
            plain = run([binary, "version", "--client", "--short"], timeout=20)
            version = (plain.out or "").strip()
        return {"available": True, "version": version, "error": "" if res.ok else res.error}

    @property
    def context(self) -> str:
        return str(self.cfg.get("k8s.context", "") or "")

    @property
    def namespace(self) -> str:
        return str(self.cfg.get("k8s.namespace", "") or "")

    def _base(self) -> Optional[List[str]]:
        binary = self.binary
        if not binary:
            return None
        base = [binary]
        if self.context:
            base += ["--context", self.context]
        return base

    def _kubectl(self, args: List[str], timeout: float = 30.0, use_namespace: bool = True,
                 namespace: Optional[str] = None) -> CmdResult:
        base = self._base()
        if base is None:
            return CmdResult(False, 127, error=t("k8s.kubectlMissing"), missing=True)
        target_ns = namespace if namespace is not None else self.namespace
        cmd = list(base)
        if use_namespace and target_ns and "-n" not in args and "--all-namespaces" not in args \
                and "-A" not in args:
            cmd += ["-n", target_ns]
        cmd += args
        return run(cmd, timeout=timeout)

    def _json(self, resource: str, *extra: str, use_namespace: bool = True,
              namespace: Optional[str] = None, timeout: float = 40.0) -> Dict[str, Any]:
        res = self._kubectl(["get", resource, "-o", "json", *extra], timeout=timeout,
                            use_namespace=use_namespace, namespace=namespace)
        if not res.ok:
            return {"ok": False, "error": (res.err or res.error or t("k8s.kubectlError")).strip()[:400],
                    "missing": res.missing}
        data = json_any(res.out)
        if not isinstance(data, dict):
            return {"ok": False, "error": t("k8s.parseFailed")}
        return {"ok": True, "data": data}

    @staticmethod
    def _items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not payload.get("ok"):
            return []
        data = payload.get("data", {})
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    # ------------------------------------------------------------------ #
    #  Обзор
    # ------------------------------------------------------------------ #
    def overview(self) -> Dict[str, Any]:
        status = self.available()
        result: Dict[str, Any] = {
            "available": status["available"],
            "version": status.get("version", ""),
            "error": status.get("error", ""),
            "context": self.context,
            "namespace": self.namespace or t("k8s.allNamespaces"),
            "counts": {},
            "warnings": [],
        }
        if not status["available"]:
            return result

        # Все запросы независимы — тянем их параллельно, чтобы сводка
        # не складывалась из последовательных вызовов kubectl.
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_nodes = pool.submit(self.nodes)
            future_pods = pool.submit(self.pods)
            future_deployments = pool.submit(self.deployments)
            future_services = pool.submit(self.services)
            future_events = pool.submit(self.events, 200)
            future_top = pool.submit(self.top_nodes)
            nodes = future_nodes.result()
            pods = future_pods.result()
            deployments = future_deployments.result()
            services = future_services.result()
            events = future_events.result()
            top_nodes = future_top.result()

        result["nodes"] = nodes
        result["counts"]["nodes"] = len(nodes)
        result["counts"]["nodes_ready"] = len([n for n in nodes if n["ready"]])

        result["counts"]["pods"] = len(pods)
        phases: Dict[str, int] = {}
        for pod in pods:
            phases[pod["phase"]] = phases.get(pod["phase"], 0) + 1
        result["counts"]["pods_by_phase"] = phases
        result["counts"]["pods_problem"] = len([p for p in pods
                                                if p["phase"] not in ("Running", "Succeeded")
                                                or p["restarts"] > 5])
        result["counts"]["deployments"] = len(deployments)
        result["counts"]["deployments_ready"] = len([d for d in deployments if d["ready"] == d["desired"]])
        result["counts"]["services"] = len(services)
        result["counts"]["warnings"] = len([e for e in events if e["type"] == "Warning"])

        result["top_nodes"] = top_nodes
        result["summaries"] = status.get("version", "")
        return result

    # ------------------------------------------------------------------ #
    def nodes(self) -> List[Dict[str, Any]]:
        payload = self._json("nodes", use_namespace=False)
        out: List[Dict[str, Any]] = []
        allocatable_cpu_total = 0.0
        for item in self._items(payload):
            meta = item.get("metadata", {})
            status = item.get("status", {})
            spec = item.get("spec", {})
            labels = meta.get("labels", {}) or {}
            roles = [key.split("/", 1)[1] for key in labels if key.startswith("node-role.kubernetes.io/")]
            conditions = {c.get("type"): c.get("status") for c in status.get("conditions", [])}
            addresses = {a.get("type"): a.get("address") for a in status.get("addresses", [])}
            node_info = status.get("nodeInfo", {}) or {}
            capacity = status.get("capacity", {}) or {}
            allocatable = status.get("allocatable", {}) or {}
            cpu_cap = parse_cpu(capacity.get("cpu"))
            mem_cap = parse_qty(capacity.get("memory"))
            allocatable_cpu_total += parse_cpu(allocatable.get("cpu"))
            pod_cap = int(float(capacity.get("pods", 0) or 0))
            out.append({
                "name": meta.get("name", ""),
                "roles": roles or ["worker"],
                "ready": conditions.get("Ready") == "True",
                "unschedulable": bool(spec.get("unschedulable")),
                "internal_ip": addresses.get("InternalIP", ""),
                "kubelet": node_info.get("kubeletVersion", ""),
                "os_image": node_info.get("osImage", ""),
                "runtime": node_info.get("containerRuntimeVersion", ""),
                "arch": node_info.get("architecture", ""),
                "cpu_cores": cpu_cap,
                "mem_total": mem_cap,
                "mem_total_human": human_bytes(mem_cap),
                "pods_capacity": pod_cap,
                "pressure": {
                    "memory": conditions.get("MemoryPressure") == "True",
                    "disk": conditions.get("DiskPressure") == "True",
                    "pid": conditions.get("PIDPressure") == "True",
                },
                "taints": [t.get("key", "") for t in spec.get("taints", []) or []],
                "created": meta.get("creationTimestamp"),
            })
        return out

    def pods(self, namespace: Optional[str] = None, all_namespaces: bool = True) -> List[Dict[str, Any]]:
        extra = ["--all-namespaces"] if all_namespaces and not (namespace or self.namespace) else []
        payload = self._json("pods", *extra, use_namespace=not bool(all_namespaces))
        out: List[Dict[str, Any]] = []
        for item in self._items(payload):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", []) or []
            restarts = sum(int(cs.get("restartCount", 0) or 0) for cs in container_statuses)
            ready_count = len([cs for cs in container_statuses if cs.get("ready")])
            reason = status.get("reason", "")
            waiting_reasons = []
            for cs in container_statuses:
                state = cs.get("state", {}) or {}
                if state.get("waiting", {}).get("reason"):
                    waiting_reasons.append(state["waiting"]["reason"])
                if state.get("terminated", {}).get("reason"):
                    waiting_reasons.append(state["terminated"]["reason"])
            out.append({
                "name": meta.get("name", ""),
                "namespace": meta.get("namespace", ""),
                "node": spec.get("nodeName", ""),
                "phase": status.get("phase", "Unknown"),
                "ready": f"{ready_count}/{len(container_statuses) or len(spec.get('containers', []))}",
                "restarts": restarts,
                "reason": reason or (waiting_reasons[0] if waiting_reasons else ""),
                "started": status.get("startTime"),
                "images": [c.get("image", "") for c in spec.get("containers", [])],
                "owner": (meta.get("ownerReferences") or [{}])[0].get("kind", ""),
                "labels": meta.get("labels", {}) or {},
            })
        out.sort(key=lambda p: (p["namespace"], p["name"]))
        return out

    def deployments(self, namespace: Optional[str] = None, all_namespaces: bool = True) -> List[Dict[str, Any]]:
        extra = ["--all-namespaces"] if all_namespaces and not (namespace or self.namespace) else []
        payload = self._json("deployments", *extra, use_namespace=not bool(all_namespaces))
        out = []
        for item in self._items(payload):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            containers = spec.get("template", {}).get("spec", {}).get("containers", []) or []
            conditions = {c.get("type"): c.get("status") for c in status.get("conditions", []) or []}
            out.append({
                "name": meta.get("name", ""),
                "namespace": meta.get("namespace", ""),
                "desired": int(spec.get("replicas", 0) or 0),
                "ready": int(status.get("readyReplicas", 0) or 0),
                "updated": int(status.get("updatedReplicas", 0) or 0),
                "available": int(status.get("availableReplicas", 0) or 0),
                "unavailable": int(status.get("unavailableReplicas", 0) or 0),
                "images": [c.get("image", "") for c in containers],
                "progressing": conditions.get("Progressing") == "True",
                "created": meta.get("creationTimestamp"),
            })
        out.sort(key=lambda d: (d["namespace"], d["name"]))
        return out

    def services(self, namespace: Optional[str] = None, all_namespaces: bool = True) -> List[Dict[str, Any]]:
        extra = ["--all-namespaces"] if all_namespaces and not (namespace or self.namespace) else []
        payload = self._json("services", *extra, use_namespace=not bool(all_namespaces))
        out = []
        for item in self._items(payload):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            ports = []
            for port in spec.get("ports", []) or []:
                name = port.get("name") or ""
                ports.append(f"{port.get('port')}" + (f"/{port.get('protocol', 'TCP')}" if port.get("protocol") else "")
                             + (f"→{port.get('targetPort')}" if port.get("targetPort") else "")
                             + (f" ({name})" if name else ""))
            ingress = status.get("loadBalancer", {}).get("ingress", []) or []
            external = [i.get("ip") or i.get("hostname") or "" for i in ingress]
            out.append({
                "name": meta.get("name", ""),
                "namespace": meta.get("namespace", ""),
                "type": spec.get("type", "ClusterIP"),
                "cluster_ip": spec.get("clusterIP", ""),
                "external": external + (spec.get("externalIPs", []) or []),
                "ports": ports,
                "selector": spec.get("selector", {}) or {},
            })
        out.sort(key=lambda s: (s["namespace"], s["name"]))
        return out

    def namespaces(self) -> List[str]:
        payload = self._json("namespaces", use_namespace=False)
        return sorted([item.get("metadata", {}).get("name", "") for item in self._items(payload) if item.get("metadata")])

    def events(self, limit: int = 100, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        payload = self._json("events", "--all-namespaces", "--sort-by=.lastTimestamp",
                             use_namespace=False, timeout=45)
        if not payload.get("ok"):
            payload = self._json("events", "--all-namespaces", use_namespace=False, timeout=45)
        out = []
        for item in self._items(payload):
            meta = item.get("metadata", {})
            involved = item.get("involvedObject", {}) or {}
            timestamp = item.get("lastTimestamp") or item.get("eventTime") or meta.get("creationTimestamp")
            out.append({
                "time": timestamp,
                "type": item.get("type", "Normal"),
                "reason": item.get("reason", ""),
                "message": (item.get("message") or "")[:300],
                "kind": involved.get("kind", ""),
                "object": involved.get("name", ""),
                "namespace": involved.get("namespace", meta.get("namespace", "")),
                "count": item.get("count", 1),
            })
        out.reverse()
        return out[:limit]

    # ------------------------------------------------------------------ #
    def top_nodes(self) -> List[Dict[str, Any]]:
        res = self._kubectl(["top", "nodes", "--no-headers"], use_namespace=False, timeout=30)
        if not res.ok:
            return []
        out = []
        for line in res.lines():
            parts = line.split()
            if len(parts) < 3:
                continue
            out.append({
                "name": parts[0],
                "cpu": parts[1],
                "cpu_pct": parse_percent_text(parts[2]),
                "mem": parts[3] if len(parts) > 3 else "",
                "mem_bytes": parse_qty(parts[3]) if len(parts) > 3 else 0,
                "mem_pct": parse_percent_text(parts[4]) if len(parts) > 4 else 0.0,
            })
        return out

    def top_pods(self, all_namespaces: bool = True) -> List[Dict[str, Any]]:
        args = ["top", "pods", "--no-headers"]
        if all_namespaces and not self.namespace:
            args.append("--all-namespaces")
        res = self._kubectl(args, timeout=30)
        if not res.ok:
            return []
        out = []
        for line in res.lines():
            parts = line.split()
            if all_namespaces and not self.namespace and len(parts) >= 4:
                namespace, name, cpu, mem = parts[0], parts[1], parts[2], parts[3]
            elif len(parts) >= 3:
                namespace, name, cpu, mem = self.namespace or "", parts[0], parts[1], parts[2]
            else:
                continue
            out.append({"namespace": namespace, "name": name, "cpu": cpu,
                        "cpu_cores": parse_cpu(cpu), "mem": mem, "mem_bytes": parse_qty(mem)})
        return out

    # ------------------------------------------------------------------ #
    #  Действия
    # ------------------------------------------------------------------ #
    def logs(self, pod: str, namespace: str = "", container: str = "", tail: int = 300) -> Dict[str, Any]:
        pod = safe_id(pod)
        if not pod:
            return {"ok": False, "error": t("k8s.badPod")}
        args = ["logs", pod, "--tail", str(max(1, min(5000, tail)))]
        if container:
            safe_container = safe_id(container)
            if safe_container:
                args += ["-c", safe_container]
        res = self._kubectl(args, timeout=40, namespace=namespace or None)
        text = res.out or ""
        if res.err and not text:
            text = res.err
        return {"ok": res.ok or bool(text.strip()), "text": text, "error": "" if res.ok else res.error}

    def describe(self, kind: str, name: str, namespace: str = "") -> Dict[str, Any]:
        if kind.lower() not in _KIND_ALLOWED or not safe_id(name):
            return {"ok": False, "error": t("k8s.unsupportedResource")}
        res = self._kubectl(["describe", kind, name], timeout=45, namespace=namespace or None)
        return {"ok": res.ok, "text": res.out or res.err, "error": "" if res.ok else res.error}

    def scale(self, kind: str, name: str, replicas: int, namespace: str = "") -> Dict[str, Any]:
        if kind.lower() not in ("deployment", "statefulset", "replicaset", "deployments", "statefulsets"):
            return {"ok": False, "error": t("k8s.scaleOnly")}
        if not safe_id(name):
            return {"ok": False, "error": t("k8s.badName")}
        replicas = max(0, min(200, int(replicas)))
        res = self._kubectl(["scale", kind, name, f"--replicas={replicas}"],
                            timeout=60, namespace=namespace or None)
        return {"ok": res.ok, "out": res.out.strip(), "error": "" if res.ok else (res.err or res.error)[:400]}

    def rollout_restart(self, kind: str, name: str, namespace: str = "") -> Dict[str, Any]:
        if kind.lower() not in ("deployment", "statefulset", "daemonset") or not safe_id(name):
            return {"ok": False, "error": t("k8s.rolloutOnly")}
        res = self._kubectl(["rollout", "restart", f"{kind}/{name}"], timeout=60, namespace=namespace or None)
        return {"ok": res.ok, "out": res.out.strip(), "error": "" if res.ok else (res.err or res.error)[:400]}

    def rollout_undo(self, kind: str, name: str, namespace: str = "") -> Dict[str, Any]:
        if not safe_id(name):
            return {"ok": False, "error": t("k8s.badName")}
        res = self._kubectl(["rollout", "undo", f"{kind}/{name}"], timeout=60, namespace=namespace or None)
        return {"ok": res.ok, "out": res.out.strip(), "error": "" if res.ok else (res.err or res.error)[:400]}

    def delete(self, kind: str, name: str, namespace: str = "") -> Dict[str, Any]:
        if not self.cfg.get("safety.allow_k8s_delete", True):
            return {"ok": False, "error": t("k8s.deleteDisabled")}
        if kind.lower() not in _KIND_ALLOWED:
            return {"ok": False, "error": t("k8s.deleteNotAllowed")}
        if not safe_id(name):
            return {"ok": False, "error": t("k8s.badName")}
        res = self._kubectl(["delete", kind, name, "--wait=false"], timeout=90, namespace=namespace or None)
        return {"ok": res.ok, "out": res.out.strip(), "error": "" if res.ok else (res.err or res.error)[:400]}

    # ------------------------------------------------------------------ #
    #  Манифесты
    # ------------------------------------------------------------------ #
    def _guard_manifest(self, path: Path) -> Optional[str]:
        resolved = Path(path).resolve()
        if not is_within(resolved, HOME_DIR):
            return t("k8s.manifestOutside")
        if resolved.suffix not in (".yaml", ".yml", ".json") or not resolved.is_file():
            return t("k8s.manifestExpected")
        return None

    def apply_file(self, path: Path, dry_run: bool = False) -> Dict[str, Any]:
        guard = self._guard_manifest(path)
        if guard:
            return {"ok": False, "error": guard}
        args = ["apply", "-f", str(path)]
        if dry_run:
            args += ["--dry-run=client"]
        res = self._kubectl(args, timeout=120, use_namespace=False)
        return {"ok": res.ok, "out": (res.out or "").strip(), "text": (res.out or "") + (res.err or ""),
                "error": "" if res.ok else (res.err or res.error)[:600]}

    def delete_file(self, path: Path, dry_run: bool = False) -> Dict[str, Any]:
        guard = self._guard_manifest(path)
        if guard:
            return {"ok": False, "error": guard}
        if not self.cfg.get("safety.allow_k8s_delete", True):
            return {"ok": False, "error": t("k8s.deleteDisabled")}
        args = ["delete", "-f", str(path)]
        if dry_run:
            args += ["--dry-run=client"]
        res = self._kubectl(args, timeout=120, use_namespace=False)
        return {"ok": res.ok, "out": (res.out or "").strip(), "text": (res.out or "") + (res.err or ""),
                "error": "" if res.ok else (res.err or res.error)[:600]}

    def contexts(self) -> Dict[str, Any]:
        res = self._kubectl(["config", "get-contexts", "-o", "name"], timeout=20, use_namespace=False)
        if not res.ok:
            return {"ok": False, "items": [], "error": (res.err or res.error)[:300]}
        current = run([self.binary or "kubectl", "config", "current-context"], timeout=15)
        return {"ok": True, "items": res.lines(), "current": (current.out or "").strip(),
                "selected": self.context or t("k8s.defaultContext")}

    def set_target(self, context: str = "", namespace: str = "") -> Dict[str, Any]:
        """Переключает context/namespace; пустая строка сбрасывает к значению по умолчанию."""
        if context is not None:
            if context and not re.match(r"^[A-Za-z0-9@._:/-]{1,128}$", context):
                return {"ok": False, "error": t("k8s.badContext")}
            self.cfg.set("k8s.context", context)
        if namespace is not None:
            if namespace and not re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", namespace):
                return {"ok": False, "error": t("k8s.badNamespace")}
            self.cfg.set("k8s.namespace", namespace)
        self.cfg.save()
        return {"ok": True, "context": self.context, "namespace": self.namespace}


def parse_percent_text(text: str) -> float:
    match = re.search(r"([\d.]+)", str(text))
    return round(float(match.group(1)), 1) if match else 0.0


#: Кэш дорогих сводок (используется API, чтобы не долбить кластер).
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 5.0


class CachedK8s:
    """Обёртка с коротким кэшем: UI обновляется часто, кластер дёргать незачем.

    Single-flight: при промахе кэша только один поток вызывает фабрику по
    каждому ключу, остальные ждут его и забирают готовое значение из кэша.
    """

    def __init__(self, k8s: K8s) -> None:
        self.k8s = k8s
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._locks: Dict[str, threading.Lock] = {}

    def get(self, key: str, factory, ttl: float = _CACHE_TTL) -> Any:
        now = time.time()
        with _CACHE_LOCK:
            cached = self._store.get(key)
            if cached and now - cached[0] < ttl:
                return cached[1]
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
        # Пока держим блокировку ключа, повторно проверяем кэш: возможно,
        # значение уже подготовил поток, который успел раньше.
        with lock:
            now = time.time()
            with _CACHE_LOCK:
                cached = self._store.get(key)
                if cached and now - cached[0] < ttl:
                    return cached[1]
            value = factory()
            with _CACHE_LOCK:
                self._store[key] = (now, value)
            return value

    def invalidate(self) -> None:
        with _CACHE_LOCK:
            self._store.clear()
