"""Ротация журнала действий MegaTool (changes.audit).

Журнал не должен расти безгранично: при превышении лимита старые записи
уходят в audit.jsonl.N, а число архивов ограничено настройкой keep.
"""

from __future__ import annotations

import pytest


def _archives(log_path):
    """Архивные копии audit.jsonl.N (только числовые суффиксы), по возрастанию N."""
    result = []
    prefix = log_path.name + "."
    for candidate in log_path.parent.glob(prefix + "*"):
        suffix = candidate.name[len(prefix):]
        if suffix.isdigit():
            result.append((int(suffix), candidate))
    return sorted(result)


@pytest.fixture()
def audit_env(tmp_path, monkeypatch):
    """Изолированный журнал; путь и лимиты возвращаются в исходное состояние."""
    from megatool import changes

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(changes, "AUDIT_LOG", log_path)
    original = (changes._AUDIT_MAX_MB, changes._AUDIT_KEEP)
    changes.configure_audit(5.0, 3)  # предсказуемое начальное состояние
    yield changes, log_path
    changes.configure_audit(*original)


def test_audit_rotation_keeps_archives_within_limit(audit_env):
    changes, log_path = audit_env
    changes.configure_audit(max_mb=0.0000005, keep=2)

    for index in range(15):
        changes.audit("test.action", target=f"item-{index}", detail="x" * 200)
        # Лимит архивов соблюдается после каждой записи.
        assert len(_archives(log_path)) <= 2

    assert log_path.is_file()
    assert (log_path.parent / (log_path.name + ".1")).is_file()

    archives = _archives(log_path)
    assert len(archives) == 2
    assert [number for number, _ in archives] == [1, 2]
    assert not (log_path.parent / (log_path.name + ".3")).exists()

    # Журнал и архивы остаются читаемым JSONL, свежие записи — в основном файле.
    entries = changes.read_audit(limit=1000)
    assert entries and entries[0]["action"] == "test.action"
    assert entries[0]["target"] == "item-14"


def test_read_audit_uses_current_module_constant(audit_env):
    """read_audit() читает путь из актуальной константы модуля (её патчат тесты)."""
    changes, log_path = audit_env
    changes.audit("demo.action", target="tmp")

    assert log_path.is_file()
    assert not (log_path.parent / (log_path.name + ".1")).exists()

    items = changes.read_audit(limit=10)
    assert len(items) == 1
    assert items[0]["action"] == "demo.action"
    assert items[0]["target"] == "tmp"
