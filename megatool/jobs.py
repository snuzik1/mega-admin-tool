"""Менеджер фоновых задач: запуск внешних процессов с живым логом.

Используется для Ansible, compose-операций и любых длительных действий.
Каждая задача пишет лог на диск (`~/.megatool/runs/<id>.log`) и в память,
подписчики (WebSocket) получают строки по мере появления.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from .i18n import t
from .util import ensure_dir, human_duration, iso, read_json, safe_id, write_json


class JobLimitError(RuntimeError):
    """Достигнут предел одновременно выполняемых задач."""
MAX_LINES = 4000


@dataclass
class Job:
    id: str
    title: str
    cmd: List[str]
    kind: str = "generic"
    cwd: str = ""
    status: str = "pending"          # pending | running | success | failed | cancelled
    exit_code: Optional[int] = None
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    lines: deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    log_path: Optional[Path] = None
    _proc: Optional[subprocess.Popen] = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _subs: List[queue.Queue] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ------------------------------------------------------------------ #
    @property
    def duration(self) -> Optional[float]:
        if self.started is None:
            return None
        return (self.finished or time.time()) - self.started

    def to_dict(self, include_cmd: bool = True) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "status": self.status,
            "exit_code": self.exit_code,
            "created": iso(self.created),
            "started": iso(self.started) if self.started else None,
            "finished": iso(self.finished) if self.finished else None,
            "duration": round(self.duration, 1) if self.duration else None,
            "duration_human": human_duration(self.duration),
            "error": self.error,
            "meta": self.meta,
            "lines": len(self.lines),
            "cwd": self.cwd,
        }
        if include_cmd:
            data["cmd"] = self.cmd
            data["cmdline"] = " ".join(self.cmd)
        return data

    def add_line(self, text: str, stream: str = "stdout") -> None:
        item = {"ts": time.time(), "stream": stream, "text": text}
        with self._lock:
            self.lines.append(item)
            subs = list(self._subs)
        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
                    fh.write(text + "\n")
            except OSError:
                pass
        for sub in subs:
            try:
                sub.put_nowait(item)
            except Exception:
                pass

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(sub)
            for item in list(self.lines)[-200:]:  # отдаём хвост для контекста
                try:
                    sub.put_nowait(item)
                except Exception:
                    break
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)


class JobManager:
    """Хранит активные и последние завершённые задачи."""

    def __init__(self, runs_dir: Path, keep: int = 60, max_active: int = 8) -> None:
        self.runs_dir = ensure_dir(runs_dir)
        self.keep = keep
        self.max_active = max(0, int(max_active or 0))
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._lock = threading.RLock()
        self._load_history()

    # ------------------------------------------------------------------ #
    def _load_history(self) -> None:
        """Поднимает метаданные прошлых запусков, чтобы список не пустел после рестарта."""
        files = sorted(self.runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[: self.keep]:
            data = read_json(path, default=None)
            if not isinstance(data, dict) or not data.get("id"):
                continue
            job = Job(
                id=str(data["id"]),
                title=str(data.get("title", "")),
                cmd=list(data.get("cmd", []) or []),
                kind=str(data.get("kind", "generic")),
                cwd=str(data.get("cwd", "")),
                status=str(data.get("status", "success")),
                exit_code=data.get("exit_code"),
                created=float(data.get("created_ts") or path.stat().st_mtime),
                error=str(data.get("error", "")),
                meta=data.get("meta", {}) or {},
            )
            job.started = data.get("started_ts")
            job.finished = data.get("finished_ts")
            log_path = path.with_suffix(".log")
            job.log_path = log_path if log_path.is_file() else None
            self._jobs[job.id] = job
            self._order.append(job.id)

    def _persist(self, job: Job) -> None:
        write_json(self.runs_dir / f"{job.id}.json", {
            "id": job.id,
            "title": job.title,
            "cmd": job.cmd,
            "kind": job.kind,
            "cwd": job.cwd,
            "status": job.status,
            "exit_code": job.exit_code,
            "error": job.error,
            "meta": job.meta,
            "created_ts": job.created,
            "started_ts": job.started,
            "finished_ts": job.finished,
        })

    # ------------------------------------------------------------------ #
    def submit(
        self,
        title: str,
        cmd: Sequence[str],
        cwd: Optional[Union[str, os.PathLike]] = None,
        kind: str = "generic",
        meta: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        stdin_text: Optional[str] = None,
    ) -> Job:
        """Создаёт задачу и запускает её в отдельном потоке.

        stdin_text, если он передан, пишется в stdin процесса служебным
        daemon-потоком и после записи пайп закрывается — большие скрипты
        не блокируют запуск задачи.
        """
        if self.max_active and self.active_count() >= self.max_active:
            raise JobLimitError(t("jobs.tooMany", limit=self.max_active))
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            title=title,
            cmd=[str(c) for c in cmd],
            kind=kind,
            cwd=str(cwd or os.getcwd()),
            meta=meta or {},
            log_path=self.runs_dir / f"{job_id}.log",
        )
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._trim()
        thread = threading.Thread(target=self._execute, args=(job, env, stdin_text), daemon=True,
                                  name=f"megatool-job-{job_id}")
        thread.start()
        return job

    def _trim(self) -> None:
        while len(self._order) > self.keep:
            old_id = self._order.pop(0)
            self._jobs.pop(old_id, None)

    # ------------------------------------------------------------------ #
    def _execute(self, job: Job, env: Optional[Dict[str, str]],
                 stdin_text: Optional[str] = None) -> None:
        if job._cancel_event.is_set():
            job.status = "cancelled"
            job.finished = time.time()
            self._persist(job)
            return
        job.started = time.time()
        job.status = "running"
        self._persist(job)
        job.add_line(f"$ {' '.join(job.cmd)}", "system")
        job.add_line(t("jobs.cwd", cwd=job.cwd), "system")
        real_env = dict(os.environ)
        if env:
            real_env.update({k: str(v) for k, v in env.items()})
        real_env.setdefault("PYTHONIOENCODING", "utf-8")
        real_env.setdefault("NO_COLOR", "1")
        real_env.setdefault("ANSIBLE_FORCE_COLOR", "0")
        real_env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")
        real_env.setdefault("ANSIBLE_STDOUT_CALLBACK", "default")

        try:
            proc = subprocess.Popen(
                job.cmd,
                cwd=job.cwd,
                env=real_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError:
            job.status = "cancelled" if job._cancel_event.is_set() else "failed"
            job.error = t("jobs.programNotFound", name=job.cmd[0])
            job.finished = time.time()
            job.add_line(f"! {job.error}", "error")
            self._persist(job)
            return
        except OSError as exc:
            job.status = "cancelled" if job._cancel_event.is_set() else "failed"
            job.error = str(exc)
            job.finished = time.time()
            job.add_line(f"! {exc}", "error")
            self._persist(job)
            return

        job._proc = proc
        if job._cancel_event.is_set():
            # Отмена пришла, пока Popen ещё не вернул процесс.
            self._kill_tree(proc)
        if stdin_text is not None and proc.stdin is not None:
            writer = threading.Thread(target=self._stdin_writer, args=(proc.stdin, stdin_text),
                                      daemon=True, name=f"megatool-job-{job.id}-stdin")
            writer.start()
        readers = [
            threading.Thread(target=self._reader, args=(job, proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._reader, args=(job, proc.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        code = proc.wait()
        for reader in readers:
            reader.join(timeout=3)

        job.exit_code = code
        job.finished = time.time()
        if job._cancel_event.is_set() or job.status == "cancelled":
            job.status = "cancelled"
        elif code == 0:
            job.status = "success"
        else:
            job.status = "failed"
        job.add_line(t("jobs.finished", status=job.status, code=code,
                       duration=human_duration(job.duration)), "system")
        self._persist(job)

    def _stdin_writer(self, pipe: Any, text: str) -> None:
        """Пишет stdin-текст и закрывает пайп (в daemon-потоке, чтобы не блокировать)."""
        try:
            pipe.write(text)
            pipe.flush()
        except (ValueError, OSError):
            pass
        finally:
            try:
                pipe.close()
            except (ValueError, OSError):
                pass

    def _reader(self, job: Job, pipe: Any, stream: str) -> None:
        if pipe is None:
            return
        try:
            for line in iter(pipe.readline, ""):
                job.add_line(line.rstrip("\r\n"), stream)
        except (ValueError, OSError):
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def cancel(self, job_id: str) -> Dict[str, Any]:
        job = self.get(job_id)
        if not job:
            return {"ok": False, "error": t("jobs.notFound")}
        if job.status not in ("pending", "running"):
            return {"ok": False, "error": t("jobs.notRunning", status=job.status)}
        job._cancel_event.set()
        job.status = "cancelled"
        job.add_line(t("jobs.cancelRequested"), "system")
        if job._proc is not None:
            self._kill_tree(job._proc)
        else:
            # Поток ещё не успел создать процесс: он увидит событие и выйдет.
            job.finished = time.time()
            self._persist(job)
        return {"ok": True}

    def _kill_tree(self, proc: subprocess.Popen) -> None:
        if psutil is not None:
            try:
                parent = psutil.Process(proc.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except Exception:
                        pass
                if children:
                    psutil.wait_procs(children, timeout=4)
                try:
                    parent.terminate()
                    parent.wait(timeout=4)
                except Exception:
                    pass
                _, alive = psutil.wait_procs([parent], timeout=2)
                for item in alive:
                    item.kill()
                return
            except Exception:
                pass
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 40, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = [self._jobs[jid] for jid in reversed(self._order) if jid in self._jobs]
        if kind:
            items = [j for j in items if j.kind == kind]
        return [j.to_dict(include_cmd=False) for j in items[:limit]]

    def log(self, job_id: str, tail: int = 400) -> Dict[str, Any]:
        job = self.get(job_id)
        if not job:
            return {"ok": False, "error": t("jobs.notFound")}
        with job._lock:
            lines = list(job.lines)[-tail:]
        if not lines and job.log_path and job.log_path.is_file():
            try:
                text = job.log_path.read_text(encoding="utf-8", errors="replace")
                lines = [{"ts": 0, "stream": "stdout", "text": ln} for ln in text.splitlines()[-tail:]]
            except OSError:
                lines = []
        return {"ok": True, "job": job.to_dict(include_cmd=False), "lines": lines}

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in ("pending", "running"))

    def cancel_all(self) -> int:
        count = 0
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status in ("pending", "running"):
                self.cancel(job.id)
                count += 1
        return count
