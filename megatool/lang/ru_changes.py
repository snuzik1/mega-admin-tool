"""Словарь серверных сообщений раздела «Что изменилось»."""

DATA = {
    "changes": {
        "files": "Изменённые файлы",
        "filesHint": "Каталоги отслеживания задаются в настройках (monitor.watch_paths)",
        "packages": "Пакеты и обновления",
        "logins": "Входы в систему",
        "services": "Службы, требующие внимания",
        "logs": "Ошибки в журналах",
        "audit": "Действия в MegaTool",
        "hintSource": "Источник: {source}",
        "hintAuditLog": "Журнал: {path}",
        "sourceUndefined": "не определён",
        "sourceUnavailable": "недоступен",
        "aptOperation": "операция apt",
        "installedOn": "по состоянию на {time}",
        "installedBy": "установил: {user}",
        "failedLogin": "неудачный вход: {user}",
        "session": "сессия: {info}",
        "sourceHotfix": "Get-HotFix (обновления Windows)",
        "sourceServices": "Get-Service (автозапуск, не запущены)",
        "sourceWinEvents": "Журнал событий Windows (Уровень: критично/ошибка)",
    },
}
