# Публикация на GitHub

Этот документ — чек-лист для мейнтейнера. Он не нужен обычному пользователю
MegaTool.

## 1. Что уже подготовлено

- `LICENSE` (MIT), `README.md`, `SECURITY.md`, `CONTRIBUTING.md`;
- `.gitignore`, `.gitattributes`, `.editorconfig`;
- GitHub Actions: `.github/workflows/ci.yml` (Linux/Windows, Python 3.10/3.12/3.14);
- шаблоны Issue и Pull Request, Dependabot;
- `docs/GUIDE.md` и `docs/ARCHITECTURE.md`;
- `CHANGELOG.md` для первого релиза.

Перед публикацией **проверьте**, что в репозиторий не попали:

- `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`;
- `~/.megatool/`, `access.json`, `config.json`, журналы и дампы;
- токены, пароли, SSH-ключи и файлы `.env`;
- личные абсолютные пути вида `C:\Users\...`.

## 2. Создайте локальный репозиторий

В каталоге проекта:

```bash
git init -b main
git add .
git status --short
git commit -m "MegaTool 0.6.0"
```

`git status` перед коммитом должен показывать только исходники, документацию и
конфигурацию CI. Если в списке есть `.venv` или кэши — проверьте `.gitignore`.

## 3. Создайте репозиторий на GitHub

1. Откройте GitHub → **New repository**.
2. Имя: `megatool` (или выберите своё).
3. Описание, например:
   `Self-hosted sysadmin and DevOps console: monitoring, Docker/Podman, load balancer, configs, Ansible, Kubernetes — one Python process, no build step.`
4. Видимость: **Public**, если проект должен быть открытым.
5. Не добавляйте README, `.gitignore` и лицензию через GitHub — они уже есть в
   проекте.

Затем привяжите remote и отправьте `main`:

```bash
git remote add origin git@github.com:OWNER/megatool.git
git push -u origin main
```

Для HTTPS используйте URL вида
`https://github.com/OWNER/megatool.git`. Замените `OWNER` на свой аккаунт или
организацию.

## 4. Обновите ссылки

После выбора `OWNER/REPO` замените placeholder в команде клонирования в
`README.md`:

```text
https://github.com/OWNER/megatool.git
```

При желании добавьте в README бейдж CI:

```markdown
[![CI](https://github.com/OWNER/megatool/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/megatool/actions/workflows/ci.yml)
```

Если планируете публиковать пакет на PyPI, добавьте `[project.urls]` в
`pyproject.toml` с реальными ссылками на репозиторий и Issues.

## 5. Настройте репозиторий

Рекомендуемые настройки:

- **About**: короткое описание и темы:
  `sysadmin`, `devops`, `monitoring`, `self-hosted`, `fastapi`,
  `vanilla-js`, `docker`, `kubernetes`, `ansible`, `load-balancer`.
- **Settings → Security → Private vulnerability reporting**: включить, чтобы
  работала ссылка из `SECURITY.md`.
- **Settings → Code security and analysis**: включить Dependabot alerts и
  secret scanning, если доступно.
- **Settings → Branches → Branch protection rules** для `main`:
  требовать прохождение CI и запретить force-push.
- **Actions → General**: разрешить только Actions из GitHub и локальные
  workflow, если политика это позволяет.

## 6. Первый релиз

1. Дождитесь зелёного CI на `main`.
2. Создайте тег и GitHub Release:

```bash
git tag -a v0.6.0 -m "MegaTool 0.6.0"
git push origin v0.6.0
```

3. В GitHub → **Releases → Draft a new release** выберите тег `v0.6.0`,
   вставьте раздел `0.6.0` из [CHANGELOG.md](../CHANGELOG.md) и опубликуйте.

## 7. Если секрет уже попал в репозиторий

Недостаточно удалить файл новым коммитом: он остаётся в истории. Немедленно
отзовите токен или ключ, затем очистите историю (`git filter-repo`) и
опубликуйте force-push. После этого проверьте GitHub secret scanning и
уведомления.

## 8. Полезные команды

```bash
git status
git log --oneline --decorate
git remote -v
git push -u origin main
```

Если GitHub CLI установлен, можно создать репозиторий без веб-интерфейса:

```bash
gh repo create megatool --public --source=. --remote=origin --push
```

Но сначала убедитесь, что GitHub CLI авторизован и выбран правильный аккаунт.
