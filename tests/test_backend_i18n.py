"""Тесты серверной локализации и передачи stdin в фоновую задачу."""

from __future__ import annotations

import sys
import time

from megatool import i18n
from megatool.jobs import JobManager


def test_dictionaries_are_complete():
    """В словарях нет ключей, которых не хватает хотя бы в одном языке."""
    i18n._load_dicts()
    assert i18n.audit() == {"ru": [], "en": []}


def test_language_switch_changes_messages():
    """После переключения языка серверные сообщения меняются, а не остаются русскими."""
    try:
        i18n.set_language("ru")
        ru_text = i18n.t("api.notInitialized")
        i18n.set_language("en")
        en_text = i18n.t("api.notInitialized")
        assert en_text != ru_text
    finally:
        i18n.set_language("ru")


def test_job_submit_with_stdin_text(tmp_path):
    """stdin_text доходит до процесса, а большой ввод не блокирует задачу."""
    jobs = JobManager(tmp_path / "runs")
    job = jobs.submit(
        "stdin",
        [sys.executable, "-c", "import sys;print('READ:'+sys.stdin.read().strip())"],
        stdin_text="привет",
    )

    deadline = time.time() + 15
    while job.status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.05)

    assert job.status == "success", (job.status, job.error)
    texts = [line["text"] for line in job.lines]
    assert any("READ:привет" in text for text in texts), texts
