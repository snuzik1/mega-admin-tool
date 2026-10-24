# MegaTool

[![CI](https://github.com/snuzik1/mega-admin-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/snuzik1/mega-admin-tool/actions/workflows/ci.yml)

**Версия:** 0.7.0 · **Платформа:** Linux · **Python:** 3.8+ · **Лицензия:** MIT · **Интерфейс:** RU/EN · **Статус:** alpha

**MegaTool** — открытая (MIT) локальная веб-консоль администратора и DevOps
для Linux. Один Python-процесс отдаёт и API, и интерфейс: мониторинг сервера,
произвольные серверы и сервисы, контейнеры, балансировщик нагрузки, редактор
конфигов, каталог приложений, наборы конфигов, автоматическая настройка
серверов по SSH, Ansible и Kubernetes — всё в браузере, без облаков и внешних
сервисов. Windows и macOS не поддерживаются.

* Два языка интерфейса: **русский и английский** (переключатель в верхней панели
  или в настройках). Серверные сообщения, алерты и каталог приложений тоже
  локализованы.
* Чёрно-белая основа и пять тем на выбор: «бумага и тушь», сепия, сталь,
  тёмная и контрастная (плюс авто по системе), графики на canvas без внешних
  библиотек и CDN.
* **Проверки доступности**: HTTP(S)-адреса, TCP-порты и TLS-сертификаты с
  историей, задержкой, алертами и экспортом отчёта.
* **Мониторинг серверов**: свободный инвентарь TCP/HTTP-проверок с временем
  работы, простоем и временем в выключенном состоянии; карточка на дашборде и
  уведомления о смене состояния.
* **Без встроенной авторизации**: приложение рассчитано на локальный запуск или
  доверенный периметр. Нет пользователей, пресетов, сессий, токена доступа и
  отдельного входа; сетевую защиту обеспечивают loopback-режим, проверки
  `Host`/`Origin`, CSP и песочница файлов.
* Работает офлайн: никаких CDN, шрифтов из интернета и телеметрии.
* **Адаптивный интерфейс**: на телефоне и планшете меню прячется в бургер,
  карточки и таблицы перестраиваются под узкий экран.
* **Удалённые агенты**: лёгкий скрипт без зависимостей для мониторинга других
  машин; их нагрузка видна на дашборде рядом с локальной.
* Внешние инструменты (`docker`, `podman`, `kubectl`, `ansible-playbook`,
  `nginx`, `ssh`) подключаются по желанию — без них приложение работает,
  а соответствующие разделы аккуратно сообщают, чего не хватает.

## Что нового в 0.7.0

* **Python 3.8+ вместо 3.10+**: все аннотации приведены к `typing`
  (`List`, `Dict`, `Optional` и т. п.), убраны вызовы из 3.9+, зависимости
  закреплены совместимыми диапазонами, CI проверяет Python 3.8, 3.12 и 3.14
  на Ubuntu.
* **Запуск на Linux одной командой**: `quickstart.sh` скачивает архив
  репозитория и запускает `run.sh`; `start.py` сам создаёт `.venv` и ставит
  зависимости. `run.sh` (POSIX `sh`) при отсутствии Python/venv вызывает
  `install.sh`, который ставит системные пакеты. `--help` и `--version` не
  создают `.venv`.
* **Только Linux**: удалены поддержка Windows и macOS, Windows-обёртки
  `start.cmd`/`run.ps1`, PowerShell-скрипты и платформенные ветки в коде,
  а также Windows/macOS-профили CI и симуляции запуска.
* **Система доступа удалена**: пользователи, пресеты прав, сессии, мастер-токен,
  раздел «Доступ» и окна входа/настройки пароля больше не входят в проект.
  Агентские токены мониторинга сохранены — это отдельный механизм.
* **Ускорения**: кэшируются шаблоны переводов и разбор точечных путей настроек,
  HTTP-middleware безопасности объединены в один проход, с горячего пути API
  убраны проверки прав. Импорт настроек принимает только известные секции и
  отбрасывает неизвестные ключи.
* **Понятнее интерфейс**: уточнены названия разделов, статусы внешних
  инструментов вынесены в наглядную панель, добавлена справка по `F1`,
  ограничения среды свёрнуты в боковом меню, заголовки страниц и мобильная
  вёрстка приведены к единому виду.

## Разделы

Краткие инструкции по каждому разделу — в [docs/GUIDE.md](docs/GUIDE.md).

| Раздел | Что делает |
| --- | --- |
| **Обзор** (dashboard) | CPU/память/диски/сеть/температура в реальном времени, графики, алерты по порогам, сведения о системе, топ процессов, соединения, нагрузка удалённых агентов |
| **Проверки сервисов** | HTTP(S)-адреса, TCP-порты и TLS-сертификаты: фоновые проверки, история, задержка, срок действия сертификата, алерты, экспорт JSON/CSV |
| **Серверы** | Свободный инвентарь серверов и сервисов: TCP/HTTP-проверки, состояние, uptime, простой и время в выключенном состоянии, фоновый опрос, карточка на дашборде и уведомления |
| **Контейнеры** | Docker/Podman: список, нагрузка, логи, inspect, действия (start/stop/restart/pause/remove), compose-проекты, образы, тома, сети, `df` движка |
| **Балансировщик** | Реверс-прокси на asyncio: round-robin, взвешенный, least-conn, ip-hash, random; health-checks TCP/HTTP; графики по узлам; экспорт в nginx/haproxy/JSON |
| **Каталог приложений** | Каталог из 29 рецептов (compose-стеки, k8s-манифесты, наборы конфигов). Развёртывание в `~/.megatool/apps`, управление, логи, удаление |
| **Наборы файлов** | Именованные наборы файлов (compose, nginx, ansible, `.env`, манифесты) с готовыми шаблонами: «Сервер для разработки», «Веб-сервер nginx», «Docker-хост», «Мониторинг», «Приложение в Kubernetes». Редактор, добавление файлов, экспорт в ZIP |
| **Файлы и конфиги** | Дерево рабочего каталога, редактор с вкладками и черновиками (sessionStorage), поиск по содержимому, 13 шаблонов, проверка синтаксиса (nginx -t, compose config, k8s dry-run, YAML), загрузка файлов |
| **Ansible** | Инвентарь и playbooks, запуск `ansible-playbook` с `--check`/`--diff`/`--limit`, живой лог через WebSocket, `ansible ping` по хостам |
| **Настройка по SSH** | Реестр серверов, проверка инвентаря (ОС, sudo, Docker/nginx/ufw), задачи-рецепты (обновление пакетов, базовые утилиты, Docker, nginx, firewall, fail2ban, swap, пользователь, timezone, hardening SSH), генерация Ansible-плейбука, свободная команда |
| **Kubernetes** | Обзор кластера, узлы, поды (логи/inspect/удаление), деплойменты (scale/restart/undo), сервисы, события, `apply`/`delete` манифестов с dry-run |
| **Изменения** | «Что произошло за N часов»: файлы, пакеты, входы, сервисы, журналы, действия MegaTool |
| **Задачи** | Долгие операции с живым логом, отменой и историей |
| **Настройки** | Язык и тема, уведомления браузера, пороги, частоты опроса, удалённые агенты, пути к инструментам (включая ssh/scp), безопасность, имена Host, ротация аудита, лимиты задач, экспорт/импорт настроек |

## Быстрый старт

### Скачать и запустить одной командой

На чистой Linux-машине или виртуалке не нужны git и ручная установка Python:

```bash
curl -fsSL https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | sh
```

Если `curl` нет, используйте `wget`:

```bash
wget -qO- https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | sh
```

`quickstart.sh` скачает архив репозитория в `~/.local/share/megatool`
(по умолчанию), установит недостающие системные пакеты и Python-зависимости
и запустит MegaTool. Повторный запуск использует уже скачанный код:

```bash
~/.local/share/megatool/run.sh
```

Флаги MegaTool передаются после `sh -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | \
  sh -s -- --port 9000 --no-browser
```

Обновить скачанный код до свежего `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | \
  MEGATOOL_UPDATE=1 sh
```

Другой каталог, ветка, тег или коммит задаются переменными
`MEGATOOL_INSTALL_DIR` и `MEGATOOL_REF`:

```bash
curl -fsSL https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | \
  MEGATOOL_INSTALL_DIR="$HOME/megatool" MEGATOOL_REF=main sh
```

Не запускайте всю команду через `sudo`: установщик сам запросит права только
для системных пакетов. Для воспроизводимости указывайте в `MEGATOOL_REF`
конкретный тег или коммит.

Если репозиторий уже скопирован или склонирован, достаточно одной команды:

```bash
sh run.sh
```

MegaTool работает только на Linux. Для запуска нужен Python 3.8+ с модулем
`venv`, но ставить его вручную не обязательно: `run.sh` проверит окружение
и при необходимости вызовет `install.sh`. Установщик определяет пакетный
менеджер (apt-get, dnf/yum, pacman, apk или zypper), ставит недостающие
системные пакеты (может потребоваться `sudo`) и затем `start.py` создаёт
`.venv` с пятью runtime-зависимостями. Docker, Node.js и сборка frontend
не нужны.

Если автоматическая установка не подходит, поставьте пакеты вручную:

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install python3 python3-venv

# Fedora/RHEL
sudo dnf install python3

# Arch
sudo pacman -S python

# Alpine (bash нужен для bootstrap/smoke, но не для run.sh)
sudo apk add python3 py3-pip bash build-base linux-headers
```

Автоустановку системных пакетов можно отключить:

```bash
MEGATOOL_AUTO_INSTALL=0 sh run.sh
```

Прямой `python3 start.py` при ошибке создания venv или установки pip тоже
пробует вызвать `install.sh`; переменная `MEGATOOL_AUTO_INSTALL=0` отключает
это поведение.

Python 3.8 уже не поддерживается upstream, но MegaTool пока сохраняет его
для старых дистрибутивов и проверяет в CI. Новый код должен оставаться
совместимым с 3.8 до отдельного решения о повышении минимальной версии.

### Клонирование и запуск

```bash
git clone https://github.com/snuzik1/mega-admin-tool.git
cd mega-admin-tool

# через скрипт-обёртку
bash run.sh

# на минимальном Alpine, где ещё нет bash
sh run.sh

# или напрямую
python3 start.py
```

Если файл исполняемый, можно короче:

```bash
chmod +x run.sh install.sh quickstart.sh start.py
./run.sh
```

Флаги CLI передаются как есть:

```bash
python3 start.py --port 9000 --no-browser
```

`run.sh` понимает переменные `MEGATOOL_HOST` и `MEGATOOL_PORT`:

```bash
MEGATOOL_PORT=9000 bash run.sh
MEGATOOL_HOST=127.0.0.1 MEGATOOL_PORT=9000 sh run.sh --no-browser
```

Для скачивания архива без Git подойдёт **Code → Download ZIP**; после
распаковки запускайте `sh run.sh`.

Первый запуск: `run.sh` проверяет Python/venv и при необходимости вызывает
`install.sh`; затем `start.py` создаёт `.venv` и ставит **5 runtime-зависимостей**
(обычно ~15 секунд при нормальной сети). Все следующие запуски — сразу
приложение, pip не вызывается. Логика Python-окружения остаётся в `start.py`;
`run.sh` только проверяет системные предпосылки и передаёт аргументы, а
`install.sh` ставит системные пакеты и вызывает `start.py --install-only`.

Установку можно запустить отдельно, без старта сервера:

```bash
sh install.sh                   # системные пакеты + .venv + runtime-зависимости
sh install.sh --system-only     # только системные пакеты
python3 start.py --install-only # только .venv и Python-зависимости
```

Docker, kubectl, Ansible, nginx и SSH-клиент автоматически **не** ставятся:
они необязательны, приложение работает без них. Каталог окружения можно
задать через `MEGATOOL_VENV` (относительный путь считается от каталога
проекта; переменную понимают `start.py`, `scripts/bootstrap.sh` и
`scripts/install-autostart.sh`).

### Переезд с Windows или macOS

Поддержка Windows и macOS удалена в 0.7.0: обёртки `start.cmd`/`run.ps1` и
PowerShell-скрипты больше не поставляются. Рекомендуемый путь — WSL2 с Ubuntu
или отдельная Linux-машина. Порядок переезда:

1. Установите WSL2 и Ubuntu (если ещё не установлены):

   ```powershell
   wsl --install -d Ubuntu
   ```

2. Внутри Ubuntu можно не ставить Python вручную: `run.sh` сделает это сам.
   Если нужен ручной вариант:

   ```bash
   sudo apt update
   sudo apt install python3 python3-venv
   ```

3. Клонируйте проект в домашний каталог Linux, например
   `~/mega-admin-tool`, а не в `/mnt/c/...`: так работают права, символические
   ссылки и `.venv`.

4. Если каталог скопирован с Windows, удалите старый `.venv`. Windows-venv
   содержит `Scripts/python.exe` и несовместим с Linux:

   ```bash
   rm -rf .venv
   ```

5. Запустите `sh run.sh` (или `python3 start.py`). При необходимости
   перенесите настройки через **Настройки → Данные → Экспорт/Импорт**.

Рабочий каталог `~/.megatool` у WSL-версии находится в домашнем каталоге
Linux, а не в `C:\Users\...`. Старые Windows-данные автоматически не
подхватываются.

### Для разработки

```bash
bash scripts/bootstrap.sh --dev
```

Ставит сверх runtime-набора pytest, httpx, ruff и esprima (проверки и линт).

Проверить запуск на 10 Linux-профилях без реальных машин:

```bash
.venv/bin/python scripts/startup_matrix.py
```

Скрипт подменяет subprocess-вызовы и проверяет выбор `bin/python`,
создание/повторное использование venv, установку зависимостей и передачу
флагов. Те же профили выполняются в pytest.

Приложение слушает `127.0.0.1:8787` и само открывает браузер. Отдельного входа
нет: интерфейс доступен сразу после запуска. Если открываете MegaTool по сети,
ограничьте доступ межсетевым экраном или поставьте перед ним обратный прокси
с аутентификацией.

## Автозапуск

```bash
bash scripts/install-autostart.sh           # при входе пользователя
bash scripts/install-autostart.sh --linger  # и без входа, после перезагрузки
```

Создаёт пользовательскую службу `megatool.service` (systemd) с автоперезапуском.
Статус и журнал:

```bash
systemctl --user status megatool.service
journalctl --user -u megatool.service -f
```

Удаление: `bash scripts/uninstall-autostart.sh`. Оба скрипта не трогают
запущенные вручную экземпляры и требуют уже созданное окружение (`.venv` или
каталог из `MEGATOOL_VENV`). Если окружение нестандартное, задайте
`MEGATOOL_VENV` перед установкой автозапуска — служба сохранит этот путь.

## Командная строка

```
sh run.sh [--host 127.0.0.1] [--port 8787] [--no-browser]
          [--home ПУТЬ] [--config ФАЙЛ] [--log-level info] [--reload]
```

Внутри уже созданного окружения ту же команду можно запускать напрямую:

```
.venv/bin/python -m megatool [--host 127.0.0.1] [--port 8787] [--no-browser]
                            [--home ПУТЬ] [--config ФАЙЛ] [--log-level info] [--reload]
```

* `--home` — рабочий каталог (то же, что переменная окружения `MEGATOOL_HOME`);
* `--config` — путь к `config.json`, если он лежит не в рабочем каталоге;
* `--reload` — автоперезапуск при изменении кода (для разработки).

## Рабочий каталог

По умолчанию `~/.megatool`:

```
~/.megatool/
  config.json        # все настройки (включая балансировщики, приложения, серверы)
  hosts_state.json   # состояние и накопленные uptime/простой серверов
  apps/              # развёрнутые приложения из каталога
  bundles/           # наборы конфигов (<id>/bundle.json + files/...)
  configs/           # библиотека конфигов, манифесты, экспорт балансировщиков
  runs/              # история и логи долгих задач
  logs/audit.jsonl   # журнал всех изменяющих действий (с ротацией)
```

Каталог можно менять флагом `--home`, переменной `MEGATOOL_HOME` или секцией
`server` в настройках. Все файловые операции MegaTool ограничены этим каталогом
и дополнительными корнями из настроек (монитор → «дополнительные пути»).
При обновлении со старой версии каталог `~/.megapet` переносится автоматически.

## Темы и настройки

Тема переключается в **Настройки → Интерфейс** или кнопкой в верхней панели:
светлая, сепия, сталь, тёмная, контрастная и авто. Все темы используют те же
CSS-переменные, включая цвета и штриховку графиков, поэтому переключение
мгновенное и не требует перерисовки данных.

В настройках также доступны:

* **Сервер** — адрес, порт и дополнительные имена `Host` для работы за
  обратным прокси (`server.allowed_hosts`);
* **Система** — максимальный размер и число архивов журнала аудита, лимит
  одновременных задач и число хранимых записей;
* **Интерфейс** — тема, плотность, язык и системные уведомления;
* **Данные** — экспорт и импорт настроек, сведения о каталогах и размере
  рабочей папки;
* **Агенты** — удалённые агенты мониторинга, их адреса и токены.

Числовые значения проверяются на сервере и ограничиваются безопасными
диапазонами; настройки, кроме адреса и порта сервера, применяются сразу.

## Языки

Язык хранится в `ui.language` (`ru` или `en`) и переключается:

* кнопкой **RU / EN** в верхней панели;
* в **Настройках → Интерфейс**.

Локализованы интерфейс, серверные ошибки и предупреждения, алерты, названия и
описания рецептов каталога. Технические значения (имена контейнеров, пути,
вывод внешних команд) остаются как есть. Язык по умолчанию — русский;
новые строки должны добавляться сразу в оба словаря (см. «Структура проекта»).

## Наборы файлов

Раздел **Наборы файлов** собирает файлы в один именованный пакет:

* создание из шаблона: «Сервер для разработки» (compose: PostgreSQL + Redis +
  nginx + заготовка API, `.env.example`, конфиг nginx, Ansible-плейбук,
  inventory, README), «Веб-сервер nginx», «Docker-хост», «Мониторинг
  (Prometheus + Grafana)», «Приложение в Kubernetes»;
* встроенный редактор, добавление/удаление файлов, переименование и описание;
* экспорт всего набора в ZIP (вместе с `bundle.json`);
* файлы лежат в `~/.megatool/bundles/<id>/files/...` и доступны также в
  разделе «Файлы и конфиги».

## Настройка по SSH

Раздел **Настройка по SSH** автоматизирует первичную настройку удалённых машин.
Пароли не хранятся: используется ключ или ssh-agent.

Что можно сделать из интерфейса:

1. добавить сервер (имя, host, порт, пользователь, путь к ключу);
2. нажать **Проверить** — MegaTool выполнит портативный `sh`-скрипт и покажет
   ОС, семейство пакетов, наличие `sudo` без пароля и установленные инструменты;
3. выбрать задачи: обновление индекса пакетов, базовые утилиты (curl, git,
   htop, vim, tmux, unzip, jq), timezone, swap-файл, создание пользователя с
   SSH-ключом, hardening SSH (запрет паролей и root), firewall (ufw/firewalld),
   установка Docker, nginx, fail2ban;
4. посмотреть точный скрипт (**Показать скрипт**) и выполнить его с живым
   журналом задач;
5. собрать **Ansible-плейбук** с теми же действиями (YAML можно скачать);
6. при необходимости — **свободная команда** (с отдельным подтверждением).

Требования: клиент OpenSSH (`ssh`/`scp`), доступ по ключу, для задач с sudo —
`sudo -n` без пароля. Пути к клиентам задаются в **Настройки → Инструменты**.
Отключить удалённые операции целиком можно настройкой `safety.allow_remote_exec`.

## Агенты мониторинга

Чтобы видеть нагрузку других машин, скопируйте на них один файл —
`scripts/megatool-agent.py`. Ему не нужны зависимости MegaTool (только Python
3.8+ и стандартная библиотека), а при наличии `psutil` добавляются температура
и сетевые счётчики.

```bash
# на удалённой машине
python3 megatool-agent.py --host 0.0.0.0 --port 8791 --token СЕКРЕТ
```

Затем в **Настройки → Агенты** нажмите «Добавить агента», укажите имя и адрес
`http://хост:8791`, тот же токен — и нажмите «Проверить». Карточка «Удалённые
агенты» появится на дашборде и будет обновляться каждые 20 секунд.

Агент отдаёт JSON на `/metrics`: CPU, память, swap, диск, uptime, load average,
сеть и температуру. `/health` не требует токена. Проверка вручную:

```bash
curl -H "Authorization: Bearer СЕКРЕТ" http://хост:8791/metrics
```

Безопасность: по умолчанию агент слушает только `127.0.0.1`. Если открываете
его в сеть, **обязательно** задайте токен (`--token` или переменная
`MEGATOOL_AGENT_TOKEN`); токены хранятся в `config.json` и не возвращаются
через API (в интерфейсе видно только «токен задан»). Агент не выполняет команд —
только отдаёт метрики.

## Проверки доступности

Раздел **Проверки сервисов** следит за сервисами, которые не являются контейнерами
MegaTool:

* **HTTP(S)** — ожидаемый код ответа и, по желанию, строка в теле;
* **TCP** — открыт ли порт;
* **TLS** — устанавливается ли соединение и когда истекает сертификат
  (предупреждение за N дней, по умолчанию 30).

Проверки добавляются в интерфейсе, хранятся в `config.json` (секция `checks`) и
выполняются фоновым потоком с индивидуальным интервалом. Кнопка **Проверить**
запускает одну проверку, **Проверить всё** — все включённые. Результаты и
последние 40 точек истории видны в таблице, на дашборде и в общих
предупреждениях; отчёт можно скачать в JSON или CSV.

Ограничения зашиты в сервер: не более 50 проверок, интервал 15–3600 секунд,
таймаут 1–10 секунд, тело HTTP-ответа до 64 КБ, не более трёх перенаправлений
(только HTTP/HTTPS), URL без логина и пароля. Проверки — это исходящие запросы
от имени MegaTool; не добавляйте адреса, к которым у консоли не должно быть
доступа.

## Мониторинг серверов

Раздел **Серверы** — это свободный инвентарь машин и сервисов, за которыми
нужно следить целиком, а не отдельными проверками:

* имя, тип и заметка (например, «API», «БД», «резервный узел»);
* TCP-порт или HTTP(S)-адрес, ожидаемый код и ключевое слово для HTTP;
* индивидуальный интервал (15–3600 с) и таймаут (1–10 с);
* включение/выключение мониторинга без удаления записи.

Для каждой записи видны текущее состояние, время работы (`uptime`), простой
(`downtime`) и время в выключенном состоянии. Накопленные значения хранятся в
`hosts_state.json` и переживают перезапуск; при выключении сервера текущий
uptime фиксируется, а время выключения начинает учитываться отдельно. Ручной
запуск одной записи или всех включённых доступен кнопками в разделе и API
`/api/hosts/run`.

Раздел не заменяет **Проверки сервисов** (там акцент на HTTP/TCP/TLS-проверках и
истории) и **Настройку по SSH** (там реестр машин для настройки). Это отдельная
секция `hosts` в `config.json`; лимит — 50 записей. Переход в `down` попадает
в общие предупреждения, а смена состояния и восстановление — в системные
уведомления браузера (если они включены в настройках).

## Доступ без встроенной авторизации

В 0.7.0 система пользователей, пресетов прав, сессий, мастер-токена и отдельного
входа удалена. Это осознанное решение: MegaTool — локальная консоль для одного
оператора, а не многопользовательский сервис. Права на действия определяются
правами ОС, от имени которой запущен процесс, а сетевой доступ ограничивается
интерфейсом прослушивания.

Практические правила:

* держите `server.host = 127.0.0.1` (значение по умолчанию), если консоль нужна
  только на этой машине;
* для удалённого доступа используйте SSH-туннель, VPN или обратный прокси
  с аутентификацией и TLS;
* не публикуйте порт MegaTool напрямую в интернет — встроенной проверки
  пароля/токена больше нет;
* токены удалённых агентов (`--token` у `megatool-agent.py`) не связаны с
  доступом к самой консоли и продолжают защищать агентские метрики.

Старые ключи `auth` в `config.json` игнорируются при загрузке, не возвращаются
через API/экспорт и не могут быть добавлены через `PATCH /api/settings` или
импорт настроек.

## Внешние инструменты (по желанию)

| Инструмент | Для чего | Если не установлен |
| --- | --- | --- |
| `docker` / `podman` | контейнеры, compose-стеки | раздел «Контейнеры» и compose-рецепты недоступны |
| `kubectl` | Kubernetes | раздел «Kubernetes» показывает подсказку по установке |
| `ansible-playbook` | запуск плейбуков | раздел «Ansible» ограничен инвентарём и редактором |
| `nginx` | проверка конфигов `nginx -t` | проверка просто пропускается |
| `ssh` / `scp` | настройка удалённых серверов | раздел «Настройка по SSH» ограничен |

Пути к бинарникам можно указать в **Настройки → Инструменты** (полезно, если
инструмент установлен в нестандартном месте). Там же кнопка «Проверить».

## Безопасность

MegaTool создан для локального использования и это отражено в настройках:

* по умолчанию слушает только `127.0.0.1`; запросы к `/api/*` и WebSocket
  проверяют заголовки `Host`/`Origin` (защита от DNS-rebinding и CSWSH), чужие
  имена отклоняются, пока не добавлены в `server.allowed_hosts`;
* все ответы получают CSP, `X-Frame-Options: DENY`, `nosniff`,
  `Referrer-Policy` и ограниченный `Permissions-Policy`; интерфейс работает без
  inline-скриптов и внешних CDN;
* встроенной аутентификации нет: приложение не проверяет пароль или токен.
  Для сетевого доступа используйте обратный прокси с аутентификацией, VPN или
  SSH-туннель; порт наружу лучше не открывать;
* токены удалённых агентов проверяются только агентом (`Authorization: Bearer`
  или `X-MegaTool-Agent-Token`) и не дают доступа к консоли;
* размер тела запроса ограничен 25 МБ;
* `PATCH /api/settings` и импорт принимают только известные секции; старый
  `auth` отбрасывается, неизвестные/опасные значения не применяются;
* `config.json` создаётся с правами `0600`, каталоги `~/.megatool` и `logs` — `0700`;
* все изменяющие действия пишутся в `logs/audit.jsonl`, журнал ротируется
  (`audit.max_mb`, `audit.keep`) и виден в разделе «Изменения»;
* деструктивные операции (kill процесса, удаление контейнера/подов/файлов)
  требуют подтверждения и могут быть запрещены в настройках;
* аргументы внешних команд проверяются (без shell); параметры SSH-задач
  валидируются, скрипты передаются на сервер через stdin;
* файловые операции не выходят за пределы рабочего каталога и подключённых
  корней; манифесты для k8s применяются только из рабочего каталога.

Порядок приватного раскрытия уязвимостей описан в [SECURITY.md](SECURITY.md).

## Тесты

```bash
.venv/bin/python -m pytest -q              # тесты
.venv/bin/python scripts/jscheck.py        # синтаксис JS (esprima)
.venv/bin/python -m build --wheel         # wheel и package-data
```

Те же проверки запускает GitHub Actions (`.github/workflows/ci.yml`) на Ubuntu
с Python 3.8, 3.12 и 3.14, в wheel/sdist-задаче и в чистом Alpine-контейнере,
где `run.sh` и `install.sh` сами устанавливают пакеты.

Покрыто: API и маршруты, песочница файлов, балансировщик (реальный прокси и
health-checks), рендер всех рецептов, задачи с логами и stdin, Ansible-инвентарь,
защита Host/Origin, заголовки безопасности и WebSocket, проверки доступности
(HTTP/TCP и валидация), мониторинг серверов (uptime/простой/выключение),
настройки (валидация, импорт, отбрасывание `auth`, права файла), лимит
одновременных задач, ротация аудита, локализация (полнота словарей), наборы
конфигов (создание/файлы/экспорт ZIP), SSH-серверы (CRUD, задачи, dry-run,
плейбуки, валидация), удалённый агент (настоящий HTTP-сервер и API) и живой
uvicorn с WebSocket. Изоляция — отдельный `MEGATOOL_HOME` на прогон. Полный
набор тестов проходит на Linux.

## Структура проекта

```
megatool/            # Python-пакет (backend)
  api.py            # HTTP-маршруты /api/*
  api_agents.py     # маршруты удалённых агентов мониторинга
  api_bundles.py    # маршруты наборов конфигов
  api_servers.py    # маршруты SSH-серверов
  api_checks.py     # маршруты проверок доступности
  api_hosts.py      # маршруты мониторинга серверов
  checks.py         # HTTP/TCP/TLS-проверки, история и алерты
  hosts.py          # инвентарь серверов, TCP/HTTP-пробы, uptime/простой
  agents.py         # опрос удалённых агентов (JSON по HTTP, кэш)
  server.py         # FastAPI, статика, WebSocket, Host/Origin, middleware
  runtime.py        # сборка приложения: настройки + сервисы
  collector.py      # метрики, история, алерты
  systeminfo.py     # сведения о системе и дисках
  changes.py        # отчёт «что изменилось», аудит с ротацией
  containers.py     # docker/podman
  balancer.py       # реверс-прокси и менеджер балансировщиков
  recipes.py        # каталог рецептов (+ английские названия/описания)
  catalog.py        # развёрнутые приложения
  appfiles.py       # шаблоны конфигов и валидаторы
  bundles.py        # наборы конфигов и шаблоны
  provision.py      # SSH: проверка, задачи, скрипты, плейбуки
  k8s.py            # kubectl
  ansible.py        # ansible-playbook, инвентарь
  jobs.py           # долгие задачи и логи (stdin для скриптов)
  security.py       # определение loopback-адресов
  i18n.py           # локализация серверных сообщений
  lang/             # словари backend: ru_<модуль>.py / en_<модуль>.py
  config.py         # настройки по умолчанию, пути
  util.py           # запуск процессов, helper-функции
  web/              # интерфейс без сборки, входит в wheel как package-data
    index.html
    css/app.css
    js/app.js       # роутер, тема, язык, топбар, уведомления
    js/ui.js        # компоненты (карточки, таблицы, диалоги…)
    js/charts.js    # графики на canvas
    js/i18n.js      # t(), переключение языка, аудит словарей
    js/lang/{ru,en}/ # словари интерфейса по разделам
    js/views/*.js   # 14 разделов
tests/              # pytest (включая интеграционные и матрицу запуска)
scripts/            # bootstrap, venv_path, jscheck, startup_matrix, агент, автозапуск
quickstart.sh       # скачать архив с GitHub и запустить run.sh
install.sh          # системные пакеты + вызов start.py --install-only
start.py            # единый запуск (создаёт .venv при первом старте)
run.sh              # POSIX-обёртка запуска, при необходимости вызывает install.sh
```

Подробнее об устройстве — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
планы — в [docs/ROADMAP.md](docs/ROADMAP.md).

## Участие в проекте

- [CONTRIBUTING.md](CONTRIBUTING.md) — окружение, проверки и правила PR.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — правила общения в проекте.
- [SECURITY.md](SECURITY.md) — как приватно сообщить об уязвимости.
- [CHANGELOG.md](CHANGELOG.md) — изменения по версиям.
- [docs/PUBLISHING.md](docs/PUBLISHING.md) — чек-лист публикации для мейнтейнера.

## Лицензия

MIT — см. [LICENSE](LICENSE). Делайте что хотите, только не удаляйте копирайт.

---

## English summary

**MegaTool** is an open-source (MIT) local web console for sysadmins and DevOps on
Linux, running on Python 3.8+ as a single process (FastAPI + vanilla-JS frontend,
no build step, no CDN). It monitors the host (CPU, memory, disks, network, temperatures, alerts),
manages Docker/Podman containers and compose stacks, runs a real asyncio-based
load balancer (round-robin, weighted, least-conn, ip-hash, random, health checks,
nginx/haproxy export), offers a catalogue of 29 ready-to-deploy recipes, edits and
validates config files, collects named file bundles ("Development server",
"nginx web server", "Docker host", "Monitoring", "Kubernetes app") with ZIP
export, provisions remote servers over SSH (inventory check, package/Docker/nginx
install, firewall, fail2ban, users, SSH hardening, generated Ansible playbooks),
runs Ansible playbooks with live logs, reviews Kubernetes clusters via kubectl,
reports "what changed" over a time range, and polls lightweight dependency-free
agents on remote machines (CPU/RAM/disk/network) shown on the dashboard.
A separate **Servers** inventory monitors arbitrary TCP/HTTP endpoints and tracks
uptime, downtime and disabled time, with dashboard alerts and browser
notifications. There is no built-in authentication in 0.7.0: users, permission
presets, sessions, the access token and the Access section were removed. MegaTool
is meant for local or trusted-perimeter use; keep it on loopback or put it behind
an authenticating reverse proxy, VPN or SSH tunnel. Remote-agent tokens remain a
separate mechanism. See [docs/GUIDE.md](docs/GUIDE.md) for a quick per-section
guide.
The interface is responsive: on phones the navigation collapses into a burger
menu and cards/tables adapt to the narrow screen. An autostart helper is included
for Linux (systemd user unit).

On a fresh Linux VM, one command downloads and starts everything:

```bash
curl -fsSL https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | sh
```

The UI is fully bilingual (Russian and English, switchable in the top bar or
Settings); backend messages, alerts and recipe metadata are localized as well.
The app listens on loopback by default; API and WebSocket calls are protected
against DNS rebinding and CSWSH (Host/Origin checks), responses get a strict CSP,
and an audit log with rotation records changing actions. Everything runs locally;
external tools are optional and degrade gracefully. The frontend is packaged
inside the wheel. On first launch `run.sh` can install missing Python/venv
system packages through the distro package manager (opt out with
`MEGATOOL_AUTO_INSTALL=0`). Runs on Linux; CI tests Ubuntu with Python 3.8,
3.12 and 3.14, the wheel contents and a clean Alpine smoke run where `run.sh`
installs the prerequisites itself.
