/* Мультиязычность интерфейса (русский / английский).

   Словари лежат в ./lang/<язык>/<раздел>.js и собираются здесь в один объект.
   Язык хранится в localStorage (для мгновенного старта) и в настройках
   приложения (ui.language) — источник истины на сервере.

   Использование:
       import { t, locale } from "../i18n.js";
       t("containers.title")
       t("common.ago.minutes", { n: 5 })
*/

import ruCommon from "./lang/ru/common.js";
import enCommon from "./lang/en/common.js";
import ruNav from "./lang/ru/nav.js";
import enNav from "./lang/en/nav.js";
import ruDashboard from "./lang/ru/dashboard.js";
import enDashboard from "./lang/en/dashboard.js";
import ruContainers from "./lang/ru/containers.js";
import enContainers from "./lang/en/containers.js";
import ruBalancer from "./lang/ru/balancer.js";
import enBalancer from "./lang/en/balancer.js";
import ruCatalog from "./lang/ru/catalog.js";
import enCatalog from "./lang/en/catalog.js";
import ruConfigs from "./lang/ru/configs.js";
import enConfigs from "./lang/en/configs.js";
import ruAnsible from "./lang/ru/ansible.js";
import enAnsible from "./lang/en/ansible.js";
import ruK8s from "./lang/ru/k8s.js";
import enK8s from "./lang/en/k8s.js";
import ruChanges from "./lang/ru/changes.js";
import enChanges from "./lang/en/changes.js";
import ruJobs from "./lang/ru/jobs.js";
import enJobs from "./lang/en/jobs.js";
import ruSettings from "./lang/ru/settings.js";
import enSettings from "./lang/en/settings.js";
import ruBundles from "./lang/ru/bundles.js";
import enBundles from "./lang/en/bundles.js";
import ruServers from "./lang/ru/servers.js";
import enServers from "./lang/en/servers.js";
import ruChecks from "./lang/ru/checks.js";
import enChecks from "./lang/en/checks.js";
import ruHosts from "./lang/ru/hosts.js";
import enHosts from "./lang/en/hosts.js";
import ruAccess from "./lang/ru/access.js";
import enAccess from "./lang/en/access.js";

export const SUPPORTED_LANGS = ["ru", "en"];
export const LANG_STORAGE_KEY = "megatool.lang";

export const DICTS = {
  ru: {
    ...ruCommon, ...ruNav, ...ruDashboard, ...ruContainers, ...ruBalancer,
    ...ruCatalog, ...ruConfigs, ...ruAnsible, ...ruK8s, ...ruChanges,
    ...ruJobs, ...ruSettings, ...ruBundles, ...ruServers,
    ...ruChecks, ...ruHosts, ...ruAccess,
  },
  en: {
    ...enCommon, ...enNav, ...enDashboard, ...enContainers, ...enBalancer,
    ...enCatalog, ...enConfigs, ...enAnsible, ...enK8s, ...enChanges,
    ...enJobs, ...enSettings, ...enBundles, ...enServers,
    ...enChecks, ...enHosts, ...enAccess,
  },
};

let current = "ru";

function lookup(dict, path) {
  let node = dict;
  for (const part of String(path).split(".")) {
    if (node === null || node === undefined || typeof node !== "object") return undefined;
    node = node[part];
  }
  return node;
}

function format(template, params) {
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    params && params[name] !== undefined && params[name] !== null ? String(params[name]) : match);
}

/** Перевод по ключу. params подставляются в {плейсхолдеры}. */
export function t(path, params) {
  let value = lookup(DICTS[current], path);
  if (value === undefined) value = lookup(DICTS.ru, path);  // запасной язык — русский
  if (value === undefined) return path;
  if (Array.isArray(value)) return value;
  if (typeof value === "string") return params ? format(value, params) : value;
  return value;
}

export function getLang() {
  return current;
}

/** Переключает язык интерфейса; возвращает установленный код языка. */
export function setLang(lang, { persist = true, silent = false } = {}) {
  current = SUPPORTED_LANGS.includes(lang) ? lang : "ru";
  document.documentElement.lang = current;
  if (persist) {
    try {
      localStorage.setItem(LANG_STORAGE_KEY, current);
    } catch (error) {
      /* приватный режим — живём без сохранения */
    }
  }
  if (!silent) window.dispatchEvent(new Event("megatool:lang"));
  return current;
}

/** Начальный язык: сохранённый в браузере → язык системы → русский. */
export function detectLang() {
  try {
    const saved = localStorage.getItem(LANG_STORAGE_KEY);
    if (saved && SUPPORTED_LANGS.includes(saved)) return saved;
  } catch (error) {
    /* ignore */
  }
  const nav = (navigator.language || "").slice(0, 2).toLowerCase();
  return SUPPORTED_LANGS.includes(nav) ? nav : "ru";
}

/** Локаль для toLocaleString / сортировки. */
export function locale() {
  return current === "ru" ? "ru-RU" : "en-US";
}

/** Языки для UI: подписи в переключателе. */
export const LANG_LABELS = { ru: "RU", en: "EN" };

/** Служебно: ключи, которых не хватает в каком-то из словарей (проверка полноты). */
export function auditDictionaries() {
  const missing = { en: [], ru: [] };
  const skip = new Set(["common.units"]);
  const walk = (node, prefix, target) => {
    for (const [key, value] of Object.entries(node || {})) {
      const path = prefix ? `${prefix}.${key}` : key;
      if (value && typeof value === "object" && !Array.isArray(value)) {
        walk(value, path, target);
      } else if (!skip.has(path)) {
        const other = target === "en" ? DICTS.en : DICTS.ru;
        if (lookup(other, path) === undefined) missing[target].push(path);
      }
    }
  };
  walk(DICTS.ru, "", "en");
  walk(DICTS.en, "", "ru");
  return missing;
}

setLang(detectLang(), { persist: false, silent: true });
