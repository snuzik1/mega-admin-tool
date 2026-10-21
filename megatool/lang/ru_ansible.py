"""Русские сообщения модуля Ansible."""

DATA = {
    "ansible": {
        "notFound": "Ansible не найден. Установите: pip install ansible (или укажите путь в настройках)",
        "inventoryNotFound": "инвентарь не найден",
        "inventorySourceIni": "INI-файл (локальный разбор)",
        "playbookNotFound": "плейбук не найден: {playbook}",
        "playbookBinNotFound": "ansible-playbook не найден — установите Ansible или укажите путь в настройках",
        "playbookBinMissing": "ansible-playbook не найден",
        "badLimit": "некорректный --limit (допустимы буквы, цифры, , : & ! * [ ])",
        "badTags": "некорректные теги",
        "badSkipTags": "некорректные --skip-tags",
        "jobTitle": "Ansible: {name}",
        "jobLimit": " (limit: {limit})",
        "adhocTitle": "Ansible ad-hoc: {module} → {pattern}",
        "binNotFound": "ansible не найден — установите Ansible или укажите путь в настройках",
        "badModule": "некорректное имя модуля",
        "badPattern": "некорректный шаблон хостов",
    },
}
