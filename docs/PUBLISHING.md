# Публикация на GitHub

Этот документ — чек-лист для мейнтейнера. Он не нужен обычному пользователю
MegaTool.

## 1. Что уже подготовлено

- `LICENSE` (MIT), `README.md`, `SECURITY.md`, `CONTRIBUTING.md`;
- `.gitignore`, `.gitattributes`, `.editorconfig`;
- GitHub Actions: `.github/workflows/ci.yml` (Ubuntu, Python 3.8/3.12/3.14,
  проверка wheel и Alpine);
- package-data: интерфейс `megatool/web/` входит в wheel;
- шаблоны Issue и Pull Request, Dependabot;
- `docs/GUIDE.md` и `docs/ARCHITECTURE.md`;
- `CHANGELOG.md` для первого релиза.

Перед публикацией **проверьте**, что в репозиторий не попали:

- `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`;
- `~/.megatool/`, `config.json`, журналы и дампы;
- токены, пароли, SSH-ключи и файлы `.env`;
- личные абсолютные пути вида `/home/...`.

## 2. Репозиторий и проверка

Рабочий репозиторий: <https://github.com/snuzik1/mega-admin-tool>. Remote
`origin` уже указывает на него:

```bash
git remote -v
git status --short
```

`git status` перед коммитом должен показывать только исходники, документацию и
конфигурацию CI. Если в списке есть `.venv` или кэши — проверьте `.gitignore`.

Перед публикацией выполните:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python scripts/jscheck.py
.venv/bin/python scripts/startup_matrix.py
.venv/bin/python -m build
```

После сборки проверьте, что в wheel есть интерфейс, и установите его в чистое
окружение:

```bash
python -m venv /tmp/megatool-wheel
/tmp/megatool-wheel/bin/pip install dist/*.whl
cd /tmp  # чтобы не импортировался исходный megatool из текущего репозитория
/tmp/megatool-wheel/bin/python - <<'PY'
from megatool.server import WEB_DIR
assert "site-packages" in str(WEB_DIR), WEB_DIR
assert (WEB_DIR / "index.html").is_file(), WEB_DIR
print(WEB_DIR)
PY
```

## 3. Опубликуйте main

```bash
git push -u origin main
```

После первого push дождитесь зелёного CI и включите рекомендуемые настройки
репозитория из раздела 5.

## 4. Ссылки и бейдж

В `README.md` уже указаны команда клонирования и CI-бейдж, а в
`pyproject.toml` — раздел `[project.urls]` с Homepage, Repository, Issues и
Changelog. При смене владельца или имени репозитория обновите их согласованно.

Релиз 0.7.0 публикуется через GitHub Release. Начиная с этой версии
`megatool/web/` входит в wheel как package-data, поэтому пакет можно
публиковать на PyPI после проверки из предыдущего раздела: соберите wheel,
установите его в чистое окружение и убедитесь, что `WEB_DIR/index.html`
существует и сервер отдаёт интерфейс. Для загрузки установите `twine`,
выполните `twine check dist/*`, затем `twine upload dist/*`.

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
git tag -a v0.7.0 -m "MegaTool 0.7.0"
git push origin v0.7.0
```

3. В GitHub → **Releases → Draft a new release** выберите тег `v0.7.0`,
   вставьте раздел `0.7.0` из [CHANGELOG.md](../CHANGELOG.md) и опубликуйте.

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

Репозиторий уже создан. Повторный `gh repo create` не нужен: для релиза
используйте тег и GitHub Releases из раздела 6.
