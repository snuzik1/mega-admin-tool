"""Русские тексты каталога задач удалённой настройки (megatool/provision.py)."""

DATA = {
    "tasks": {
        "groups": {
            "base": "Базовое",
            "system": "Система",
            "user": "Пользователи",
            "security": "Безопасность",
            "docker": "Docker",
            "web": "Веб-сервер",
        },
        "risk": {
            "safe": "Безопасно",
            "medium": "Средний риск",
            "high": "Высокий риск",
        },
        "base-update": {
            "title": "Обновить индекс пакетов",
            "description": "Обновляет список доступных пакетов (apt/dnf/yum/pacman/zypper). Сами пакеты не обновляются.",
        },
        "base-tools": {
            "title": "Базовые утилиты",
            "description": "Устанавливает curl, git, htop, vim, tmux, unzip, jq и ca-certificates.",
            "params": {
                "extra": {
                    "label": "Дополнительные пакеты",
                    "hint": "Через пробел; необязательно.",
                },
            },
        },
        "system-tz": {
            "title": "Часовой пояс",
            "description": "Устанавливает системный часовой пояс через timedatectl (или /etc/localtime).",
            "params": {
                "tz": {
                    "label": "Часовой пояс",
                    "hint": "Например: Europe/Moscow, UTC.",
                },
            },
        },
        "system-swap": {
            "title": "Файл подкачки",
            "description": "Создаёт (или включает) /swapfile заданного размера и прописывает его в /etc/fstab.",
            "params": {
                "size_gb": {
                    "label": "Размер, ГБ",
                    "hint": "От 1 до 64 ГБ.",
                },
            },
        },
        "user-create": {
            "title": "Создать пользователя",
            "description": "Создаёт пользователя с домашним каталогом и при необходимости добавляет SSH-ключ.",
            "params": {
                "user": {
                    "label": "Имя пользователя",
                    "hint": "Строчные латинские буквы, цифры, дефис и подчёркивание.",
                },
                "sshkey": {
                    "label": "Публичный SSH-ключ",
                    "hint": "Начинается с ssh-ed25519, ssh-rsa или ecdsa-sha2-; необязательно.",
                },
            },
        },
        "security-ssh": {
            "title": "Усилить SSH",
            "description": "Отключает вход по паролю и вход root, оставляет только ключи. Сначала убедитесь, что вход по ключу работает!",
        },
        "security-fw": {
            "title": "Настроить firewall",
            "description": "Открывает порты 22, 80 и 443 в ufw или firewalld.",
            "params": {
                "backend": {
                    "label": "Реализация",
                    "hint": "auto — определить по установленным пакетам.",
                    "choices": {
                        "auto": "определить автоматически",
                    },
                },
            },
        },
        "docker-install": {
            "title": "Установить Docker",
            "description": "Ставит Docker из официального скрипта get.docker.com и добавляет пользователя в группу docker.",
            "params": {
                "user": {
                    "label": "Пользователь для группы docker",
                    "hint": "Необязательно; после добавления нужен повторный вход.",
                },
            },
        },
        "web-nginx": {
            "title": "Установить nginx",
            "description": "Устанавливает nginx, включает автозапуск и запускает службу.",
        },
        "security-fail2ban": {
            "title": "Установить fail2ban",
            "description": "Устанавливает fail2ban, включает автозапуск и запускает службу.",
        },
    },
}
