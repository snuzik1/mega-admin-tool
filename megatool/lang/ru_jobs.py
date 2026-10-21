"""Русские тексты журнала задач (megatool/jobs.py)."""

DATA = {
    "jobs": {
        "cwd": "# каталог: {cwd}",
        "programNotFound": "программа не найдена: {name}",
        "finished": "# завершено: {status} (код {code}), {duration}",
        "cancelRequested": "# получен запрос на остановку",
        "notFound": "задача не найдена",
        "notRunning": "задача не выполняется (статус {status})",
        "tooMany": (
            "Слишком много одновременных задач (лимит {limit}). Дождитесь "
            "завершения текущих или увеличьте jobs.max_active в настройках."
        ),
    }
}
