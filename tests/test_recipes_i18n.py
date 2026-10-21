"""Локализация каталога рецептов: английские названия, описания и подписи.

Язык — глобальное состояние megatool.i18n, поэтому фикстура возвращает
его в «ru» и до, и после каждого теста.
"""

from __future__ import annotations

import pytest

from megatool import recipes
from megatool.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _language_ru():
    """Каждый тест начинается и заканчивается на русском языке."""
    set_language("ru")
    yield
    set_language("ru")


def test_english_translations_cover_all_recipes():
    ids = {recipe["id"] for recipe in recipes.RECIPES}
    assert set(recipes._LOC_EN) == ids
    for recipe in recipes.RECIPES:
        entry = recipes._LOC_EN[recipe["id"]]
        assert entry.get("name", "").strip(), f"{recipe['id']}: пустое английское имя"
        assert entry.get("description", "").strip(), f"{recipe['id']}: пустое описание"
        for field in ("vars", "env"):
            translations = entry.get(field) or {}
            for item in recipe.get(field, []):
                patch = translations.get(item["key"])
                assert patch, f"{recipe['id']}: нет перевода {field}.{item['key']}"
                assert patch.get("label", "").strip(), \
                    f"{recipe['id']}: пустая подпись {field}.{item['key']}"


def test_get_recipe_english():
    ru_name = recipes.get_recipe("nginx")["name"]
    assert ru_name == "Nginx + статика"

    set_language("en")
    assert get_language() == "en"
    recipe = recipes.get_recipe("nginx")
    assert recipe["name"] == recipes._LOC_EN["nginx"]["name"]
    assert recipe["name"] != ru_name
    assert recipe["description"] == recipes._LOC_EN["nginx"]["description"]

    labels = {item["key"]: item["label"] for item in recipe["vars"]}
    assert labels["NAME"] == "Container name"
    assert labels["PORT"] == "External port"

    # значения по умолчанию — данные, их не переводим
    defaults = {item["key"]: item["default"] for item in recipe["vars"]}
    assert defaults == {"NAME": "nginx", "PORT": "8080"}


def test_get_recipe_switches_language():
    set_language("en")
    en_name = recipes.get_recipe("cfg-ssh-hardening")["name"]
    set_language("ru")
    ru_name = recipes.get_recipe("cfg-ssh-hardening")["name"]
    assert en_name == "SSH hardening"
    assert ru_name == "Ужесточение SSH"
    assert en_name != ru_name


def test_list_recipes_returns_copies():
    source = next(item for item in recipes.RECIPES if item["id"] == "nginx")

    items = recipes.list_recipes()
    assert [item["id"] for item in items] == [item["id"] for item in recipes.RECIPES]
    nginx = next(item for item in items if item["id"] == "nginx")
    assert nginx["name"] == source["name"]  # по умолчанию язык ru

    # даже в ru-режиме список — копии: правки не задевают RECIPES
    nginx["name"] = "mutated-ru"
    nginx["vars"][0]["label"] = "mutated-ru"
    assert source["name"] == "Nginx + статика"
    assert source["vars"][0]["label"] == "Имя контейнера"

    set_language("en")
    en_nginx = next(item for item in recipes.list_recipes() if item["id"] == "nginx")
    assert en_nginx["name"] == recipes._LOC_EN["nginx"]["name"]

    # глубокие копии и в en-режиме
    en_nginx["name"] = "mutated"
    en_nginx["vars"][0]["label"] = "mutated"
    en_nginx["tags"].append("mutated")
    assert source["name"] == "Nginx + статика"
    assert source["vars"][0]["label"] == "Имя контейнера"
    assert "mutated" not in source["tags"]
    assert recipes.get_recipe("nginx")["name"] == recipes._LOC_EN["nginx"]["name"]

    # поиск идёт по локализованным полям
    found = [item["id"] for item in recipes.list_recipes(query="static site")]
    assert "nginx" in found


def test_categories_kinds_and_stats():
    assert recipes.categories() == recipes.CATEGORIES
    assert recipes.kinds() == recipes.KINDS
    assert recipes.categories() is not recipes.CATEGORIES  # возвращаем копию

    set_language("en")
    category_labels = recipes.categories()
    kind_labels = recipes.kinds()
    assert category_labels["web"] == "Web servers"
    assert category_labels["configs"] == "Configs & templates"
    assert kind_labels["manifest"] == "Kubernetes manifests"
    assert kind_labels["config"] == "Config bundle"

    stats = recipes.catalog_stats()
    assert stats["total"] == len(recipes.RECIPES)
    assert stats["categories"] == category_labels
    assert stats["kinds"] == kind_labels

    labels = {item["category_label"] for item in recipes.list_recipes()}
    assert labels == set(category_labels.values())
