/* Каркас интерфейса: боковое меню, верхняя панель, маршрутизация по хэшу,
   общий WebSocket метрик, тема, язык и системные уведомления. */

import { api, openWS } from "./api.js";
import { getLang, setLang, SUPPORTED_LANGS, t } from "./i18n.js";
import { bus, button, fmtDuration, h, modal, mount, timeAgo } from "./ui.js";

/* Разделы приложения. Подписи и подсказки — из словарей (nav.<id>).
   Порядок внутри групп задаёт меню: сначала наблюдение, затем инфраструктура,
   автоматизация и системные настройки. */
const VIEWS = [
  { id: "dashboard", icon: "▤", module: "./views/dashboard.js", group: "monitoring" },
  { id: "checks", icon: "◎", module: "./views/checks.js", group: "monitoring" },
  { id: "hosts", icon: "▥", module: "./views/hosts.js", group: "monitoring" },
  { id: "changes", icon: "◔", module: "./views/changes.js", group: "monitoring" },
  { id: "jobs", icon: "▶", module: "./views/jobs.js", group: "monitoring" },
  { id: "containers", icon: "▣", module: "./views/containers.js", group: "infrastructure" },
  { id: "k8s", icon: "☸", module: "./views/k8s.js", group: "infrastructure" },
  { id: "balancer", icon: "⇄", module: "./views/balancer.js", group: "infrastructure" },
  { id: "catalog", icon: "▦", module: "./views/catalog.js", group: "infrastructure" },
  { id: "bundles", icon: "▧", module: "./views/bundles.js", group: "automation" },
  { id: "configs", icon: "✎", module: "./views/configs.js", group: "automation" },
  { id: "ansible", icon: "◆", module: "./views/ansible.js", group: "automation" },
  { id: "servers", icon: "⌁", module: "./views/servers.js", group: "automation" },
  { id: "settings", icon: "⚙", module: "./views/settings.js", group: "app" },
];

const GROUP_ORDER = ["monitoring", "infrastructure", "automation", "app"];

const app = {
  view: null,
  cleanup: null,
  state: null,
  settings: null,
  metrics: { point: null, state: null, alerts: [] },
  themePref: "light",
  ws: null,
  uptimeBase: null,
  notified: new Set(),
  hostStates: null,
  beforeNavigate: null,   // раздел может вернуть false, чтобы отменить переход (черновики)
  currentHash: "#/dashboard",
};

/* --------------------------------------------------------------------- */
/*  Верхняя панель                                                        */
/* --------------------------------------------------------------------- */

function buildToolsMenu() {
  const count = h("span", { class: "top-tools-count" }, "0/5");
  const label = h("span", { class: "top-tools-label" }, t("top.tools"));
  const summary = h("summary", {
    class: "top-tools-summary",
    title: t("top.toolsHint"),
  },
    h("span", { class: "top-tools-icon", "aria-hidden": "true" }, "⚙"),
    h("span", { class: "top-tools-dot", "aria-hidden": "true" }), label, count);
  const panel = h("div", { class: "top-tools-panel" });
  const root = h("details", { class: "top-tools" }, summary, panel);
  return { root, summary, panel, count, label };
}

function buildTopbar() {
  const host = h("span", { class: "top-host" }, "…");
  const version = h("span", { class: "top-version muted" }, "");
  const uptime = h("span", { class: "top-uptime" }, "—");
  const alerts = h("button", {
    class: "top-alert",
    type: "button",
    "aria-label": t("top.alerts", { n: 0 }),
    onclick: () => (location.hash = "#/dashboard/alerts"),
  }, "⚠ 0");
  const tools = buildToolsMenu();
  const live = h("span", { class: "top-live" }, "○");
  const helpButton = h("button", {
    type: "button",
    class: "top-help",
    title: t("top.help"),
    "aria-label": t("top.help"),
    "aria-keyshortcuts": "F1",
    onclick: () => openHelp(),
  }, "?");
  const langButton = button(getLang().toUpperCase(), () => toggleLang(), { class: "top-lang", title: t("top.lang") });
  const themeButton = button("", () => toggleTheme(), { class: "top-theme", title: t("top.theme") });
  // На узких экранах боковая панель прячется, а эта кнопка её открывает.
  const menuButton = h("button", {
    type: "button",
    class: "top-menu",
    title: t("top.menu"),
    "aria-label": t("top.menu"),
    "aria-expanded": "false",
    onclick: () => toggleMobileNav(),
  }, "☰");
  const bar = h(
    "header",
    { class: "topbar" },
    h("div", { class: "top-left" }, menuButton,
      h("a", { class: "brand", href: "#/dashboard", title: t("nav.dashboard.title") }, "MEGATOOL"), version),
    h("div", { class: "top-mid" }, host, h("span", { class: "sep" }, "•"), uptime),
    h("div", { class: "top-right" }, tools.root, alerts, live, helpButton, langButton, themeButton)
  );
  return {
    bar, host, version, uptime, alerts, tools, live,
    helpButton, langButton, themeButton, menuButton,
  };
}

/* --------------------------------------------------------------------- */
/*  Мобильное меню                                                        */
/* --------------------------------------------------------------------- */

function toggleMobileNav(force) {
  const shell = document.querySelector(".shell");
  if (!shell) return;
  const open = force === undefined ? !shell.classList.contains("nav-open") : Boolean(force);
  shell.classList.toggle("nav-open", open);
  document.body.classList.toggle("nav-locked", open);
  const menu = shell.querySelector(".top-menu");
  if (menu) menu.setAttribute("aria-expanded", open ? "true" : "false");
}

function refreshTools(refs, state) {
  if (!refs) return;
  const rows = [];
  const add = (label, stateName, status, detail = "") =>
    rows.push({ label, state: stateName, status, detail });
  const container = (state.engines && state.engines.engines) || {};

  for (const [id, label] of [["docker", "Docker"], ["podman", "Podman"]]) {
    const info = container[id];
    if (!info || !info.available) {
      add(label, "off", t("top.toolsMissing"), (info && (info.error || info.binary)) || "");
    } else if (info.daemon_ok === false) {
      add(label, "warn", t("top.toolsDaemon"), info.error || info.binary || "");
    } else {
      add(label, "ok", t("top.toolsReady"), info.binary || "");
    }
  }

  const k8s = state.k8s || {};
  add("kubectl", k8s.available ? "ok" : "off",
    k8s.available ? t("top.toolsFound") : t("top.toolsMissing"), k8s.version || "");

  const ansible = state.ansible || {};
  add("Ansible", ansible.available ? "ok" : "off",
    ansible.available ? t("top.toolsFound") : t("top.toolsMissing"), ansible.version || "");

  const noteKeys = new Set((state.notes || []).map((note) =>
    (typeof note === "string" ? note : note && note.key) || ""));
  const sshFound = !noteKeys.has("notes.noSsh");
  add("SSH", sshFound ? "ok" : "off",
    sshFound ? t("top.toolsFound") : t("top.toolsMissing"));

  const ready = rows.filter((row) => row.state === "ok").length;
  refs.count.textContent = `${ready}/${rows.length}`;
  refs.root.dataset.state = ready === rows.length ? "ok" : "warn";
  refs.summary.title = t("top.toolsHint");
  refs.summary.setAttribute("aria-label", `${t("top.tools")}: ${ready}/${rows.length}`);
  refs.label.textContent = t("top.tools");
  mount(
    refs.panel,
    h("div", { class: "top-tools-head" },
      h("strong", {}, t("top.toolsTitle")),
      h("a", {
        href: "#/settings/engines",
        class: "top-tools-link",
        onclick: () => { refs.root.open = false; },
      }, t("top.toolsConfigure"))),
    h("div", { class: "top-tools-rows" }, rows.map((row) =>
      h("div", {
        class: "tool-row",
        title: [row.status, row.detail].filter(Boolean).join(" • "),
      },
        h("span", { class: `tool-dot ${row.state}`, "aria-hidden": "true" }),
        h("span", { class: "tool-name" }, row.label),
        h("span", { class: `tool-status ${row.state}` }, row.status)))),
    h("div", { class: "top-tools-foot" }, t("top.toolsNote"))
  );
}

function refreshTopbar(refs) {
  if (!refs) return;
  const state = app.state;
  if (state) {
    const info = state.app || {};
    refs.version.textContent = `v${info.version || ""}`;
    app.uptimeBase = app.uptimeBase || { at: Date.now(), uptime: info.uptime || 0 };
    const seconds = app.uptimeBase.uptime + (Date.now() - app.uptimeBase.at) / 1000;
    refs.uptime.textContent = t("top.uptime", { duration: fmtDuration(seconds) });
    const alerts = state.alerts || [];
    const critical = alerts.filter((item) => item.severity === "critical").length;
    refs.alerts.textContent = `⚠ ${alerts.length}`;
    refs.alerts.setAttribute("aria-label", t("top.alerts", { n: alerts.length }));
    refs.alerts.classList.toggle("has-critical", critical > 0);
    refs.alerts.title = alerts.length
      ? alerts.slice(0, 8).map((item) => `${item.severity}: ${item.title}`).join("\n")
      : t("top.noAlerts");
    refreshTools(refs.tools, state);
    const monitor = state.monitor || {};
    refs.host.textContent = monitor.hostname || state.hostname || "";
    refs.host.title = monitor.last_point_iso ? t("top.lastSample", { ago: timeAgo(monitor.last_point_iso) }) : "";
  }
  if (refs.live) {
    refs.live.title = t("top.live");
    if (!refs.live.classList.contains("on")) refs.live.textContent = `○ ${t("top.offline")}`;
  }
  if (refs.helpButton) {
    refs.helpButton.title = t("top.help");
    refs.helpButton.setAttribute("aria-label", t("top.help"));
  }
  if (refs.langButton) {
    refs.langButton.textContent = getLang().toUpperCase();
    refs.langButton.title = t("top.lang");
  }
  if (refs.themeButton) {
    refs.themeButton.title = t("top.theme");
    refs.themeButton.textContent = themeGlyph(app.themePref, true);
  }
}

/* --------------------------------------------------------------------- */
/*  Справка по интерфейсу                                                 */
/* --------------------------------------------------------------------- */

function openHelp() {
  const groups = [
    ["monitoring", t("nav.groups.monitoring")],
    ["infrastructure", t("nav.groups.infrastructure")],
    ["automation", t("nav.groups.automation")],
    ["app", t("nav.groups.app")],
  ];
  const terms = [
    ["checks", t("nav.checks.title")],
    ["hosts", t("nav.hosts.title")],
    ["servers", t("nav.servers.title")],
    ["catalog", t("nav.catalog.title")],
    ["bundles", t("nav.bundles.title")],
    ["configs", t("nav.configs.title")],
  ];
  const shortcuts = [
    ["theme", "Alt+T"],
    ["help", "F1"],
    ["escape", "Esc"],
  ];
  modal({
    title: t("help.title"),
    width: 660,
    body: h("div", { class: "help" },
      h("p", { class: "help-lead" }, t("help.lead")),
      h("h4", {}, t("help.startTitle")),
      h("ol", { class: "help-steps" },
        ["overview", "checks", "hosts", "tools"].map((key) => h("li", {}, t(`help.start.${key}`)))),
      h("h4", {}, t("help.groupsTitle")),
      h("div", { class: "help-groups" }, groups.map(([key, label]) =>
        h("div", { class: "help-group" },
          h("b", {}, label),
          h("span", {}, t(`help.groups.${key}`))))),
      h("h4", {}, t("help.termsTitle")),
      h("dl", { class: "help-terms" }, terms.map(([key, label]) =>
        h("div", { class: "help-term" },
          h("dt", {}, label),
          h("dd", {}, t(`help.terms.${key}`))))),
      h("h4", {}, t("help.shortcutsTitle")),
      h("div", { class: "help-shortcuts" }, shortcuts.map(([key, keys]) =>
        h("div", { class: "help-shortcut" },
          h("span", {}, t(`help.shortcuts.${key}`)),
          h("kbd", {}, keys))))),
    actions: [{ label: t("common.buttons.close"), kind: "primary" }],
  });
}

async function refreshState() {
  try {
    app.state = await api.get("/api/state");
    // /api/state отдаёт {ok, data}, как и большинство ручек
    app.state = app.state.data || app.state;
    refreshTopbar(app.topbar);
    bus.emit("state", app.state);
    maybeNotifyHosts((app.state.hosts || {}).items || []);
    maybeNotify(app.state.alerts || []);
  } catch (error) {
    console.warn("state refresh failed", error);
  }
}

/* --------------------------------------------------------------------- */
/*  Тема (светлая / тёмная / авто)                                        */
/* --------------------------------------------------------------------- */

const colorScheme = typeof window.matchMedia === "function"
  ? window.matchMedia("(prefers-color-scheme: dark)")
  : null;

const THEME_CYCLE = ["light", "sepia", "slate", "dark", "contrast", "auto"];
const THEME_GLYPHS = {
  light: "☀", sepia: "◑", slate: "◒", dark: "☾", contrast: "◓", auto: "◐",
};

function resolveTheme(preference) {
  if (preference === "auto") return colorScheme && colorScheme.matches ? "dark" : "light";
  return THEME_CYCLE.includes(preference) && preference !== "auto" ? preference : "light";
}

function themeGlyph(preference, resolved = false) {
  if (preference === "auto" && !resolved) return THEME_GLYPHS.auto;
  return THEME_GLYPHS[preference] || THEME_GLYPHS.light;
}

function applyTheme(preference) {
  app.themePref = THEME_CYCLE.includes(preference) ? preference : "light";
  const resolved = resolveTheme(app.themePref);
  document.documentElement.dataset.theme = resolved;
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) meta.content = resolved === "dark" ? "dark" : "light";
  window.dispatchEvent(new Event("megatool:theme"));
  const themeButton = app.topbar && app.topbar.themeButton;
  if (themeButton) {
    themeButton.textContent = themeGlyph(app.themePref);
    themeButton.title = t("top.theme");
  }
}

if (colorScheme && typeof colorScheme.addEventListener === "function") {
  colorScheme.addEventListener("change", () => {
    if (app.themePref === "auto") applyTheme("auto");
  });
}

// Раздел «Настройки» может переключить тему напрямую этим событием.
window.addEventListener("megatool:theme-set", (event) => {
  const theme = event && event.detail ? event.detail.theme : null;
  if (theme) applyTheme(theme);
});

function applyDensity(density) {
  app.density = density === "compact" ? "compact" : "normal";
  document.documentElement.dataset.density = app.density;
  window.dispatchEvent(new Event("megatool:density"));
}

async function toggleTheme() {
  const index = THEME_CYCLE.indexOf(app.themePref);
  const next = THEME_CYCLE[(index + 1) % THEME_CYCLE.length];
  applyTheme(next);
  try {
    await api.patch("/api/settings", { ui: { theme: next } });
  } catch (error) {
    /* настройки могли быть только для чтения — не критично */
  }
}

/* --------------------------------------------------------------------- */
/*  Язык                                                                  */
/* --------------------------------------------------------------------- */

async function setLanguage(lang, { persist = true } = {}) {
  if (!SUPPORTED_LANGS.includes(lang)) return;
  // Черновик редактора не должен исчезнуть при перерисовке: если он реально
  // есть, сохраняем язык и перезагружаем страницу (черновики лежат в sessionStorage).
  const hasDrafts = typeof app.hasUnsavedDraft === "function" && Boolean(app.hasUnsavedDraft());
  setLang(lang, { silent: true });
  if (persist) {
    try {
      await api.patch("/api/settings", { ui: { language: lang } });
    } catch (error) {
      /* сервер недоступен — язык всё равно переключился локально */
    }
  }
  if (hasDrafts) {
    window.location.reload();
    return;
  }
  renderNavLabels();
  refreshTopbar(app.topbar);
  document.title = `${t(`nav.${app.view || "dashboard"}.title`)} — MEGATOOL`;
  await navigate();
}

function toggleLang() {
  const order = SUPPORTED_LANGS;
  const next = order[(order.indexOf(getLang()) + 1) % order.length];
  setLanguage(next);
}

function renderNavLabels() {
  document.querySelectorAll(".nav-item[data-view]").forEach((element) => {
    const id = element.dataset.view;
    element.title = t(`nav.${id}.desc`);
    const label = element.querySelector(".nav-label");
    if (label) label.textContent = t(`nav.${id}.title`);
  });
  document.querySelectorAll(".nav-group-label[data-group]").forEach((element) => {
    element.textContent = t(`nav.groups.${element.dataset.group}`);
  });
  const tagline = document.querySelector(".sidebar-tagline");
  if (tagline) tagline.textContent = t("top.tagline");
  const notes = document.querySelector(".nav-notes");
  if (notes) renderNotes(notes);
}

/* --------------------------------------------------------------------- */
/*  Системные уведомления о critical-алертах и состоянии серверов          */
/* --------------------------------------------------------------------- */

function notificationsAllowed() {
  const ui = (app.settings && app.settings.ui) || {};
  return Boolean(ui.notifications && typeof Notification !== "undefined"
    && Notification.permission === "granted");
}

function browserNotify(body) {
  if (!notificationsAllowed()) return;
  try {
    new Notification(t("top.notifyTitle"), { body });
  } catch (error) {
    /* браузер может запретить — молча продолжаем */
  }
}

function maybeNotify(alerts) {
  if (!notificationsAllowed()) return;
  for (const alert of alerts || []) {
    if (alert.severity !== "critical") continue;
    const key = alert.id || alert.title;
    if (app.notified.has(key)) continue;
    app.notified.add(key);
    browserNotify(`${alert.title}${alert.detail ? " — " + alert.detail : ""}`);
  }
}

/**
 * Уведомления о переходах серверов. Используем сводку /api/state, поэтому
 * восстановление сервера тоже видно, хотя алерт из коллектора просто исчезает.
 * Для события down ставим тот же ключ, что у алерта (`host:<id>`), чтобы
 * коллектор не прислал дубликат.
 */
function maybeNotifyHosts(items) {
  const next = new Map((items || []).map((item) => [item.id, item]));
  if (app.hostStates === null) {
    app.hostStates = next;
    return;
  }
  const previousStates = app.hostStates;
  app.hostStates = next;
  if (!notificationsAllowed()) return;
  for (const [id, item] of next) {
    const previous = previousStates.get(id);
    if (!previous || previous.state === item.state) continue;
    const alertKey = `host:${id}`;
    if (item.state === "down" && previous.state === "up") {
      if (!app.notified.has(alertKey)) {
        app.notified.add(alertKey);
        browserNotify(t("hosts.notify.down", { name: item.name }));
      }
    } else if (item.state === "up" && previous.state === "down") {
      app.notified.delete(alertKey);
      browserNotify(t("hosts.notify.up", { name: item.name }));
    }
  }
}

/* --------------------------------------------------------------------- */
/*  Роутер                                                                */
/* --------------------------------------------------------------------- */

function parseHash() {
  const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
  const [path, ...rest] = raw.split("/");
  return { id: path || "dashboard", args: rest };
}

function availableViews() {
  return VIEWS;
}

async function navigate() {
  const { id, args } = parseHash();
  const views = availableViews();
  const requested = VIEWS.find((item) => item.id === id);
  const view = (requested && views.includes(requested)) ? requested : views[0];
  if (requested && view.id !== requested.id) {
    history.replaceState(null, "", `#/${view.id}`);
  }
  app.view = view.id;
  app.currentHash = `#/${view.id}`;

  // Раздел может отменить переход (например, несохранённый черновик в редакторе).
  if (app.beforeNavigate) {
    let allowed = true;
    try {
      allowed = app.beforeNavigate();
    } catch (error) {
      console.warn("beforeNavigate failed", error);
    }
    if (allowed === false) {
      app.currentHash = location.hash;  // не перекидываем хэш обратно, просто остаёмся
      return;
    }
  }

  if (app.cleanup) {
    try {
      app.cleanup();
    } catch (error) {
      console.warn("cleanup failed", error);
    }
    app.cleanup = null;
  }
  document.querySelectorAll(".nav-item").forEach((element) => {
    const active = element.dataset.view === view.id;
    element.classList.toggle("active", active);
    if (active) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
  document.title = `${t(`nav.${view.id}.title`)} — MEGATOOL`;

  // Каждый переход получает собственный контейнер: пока раздел грузится,
  // следующий переход может случиться раньше — тогда «опоздавший» раздел
  // дорисовывается в отсоединённый узел и не портит текущий экран.
  const host = document.getElementById("view");
  const container = h("div", { class: "view-body" });
  mount(host, container);
  mount(container, h("div", { class: "loading" }, h("span", { class: "spinner" }), t("common.loading")));
  const generation = (app.generation = (app.generation || 0) + 1);
  try {
    const module = await import(view.module);
    if (generation !== app.generation) return;
    const cleanup = (await module.render(container, { args, app, desc: t(`nav.${view.id}.desc`) })) || null;
    if (generation !== app.generation) {
      if (cleanup) {
        try {
          cleanup();
        } catch (error) {
          console.warn("cleanup failed", error);
        }
      }
      return;
    }
    app.cleanup = cleanup;
  } catch (error) {
    console.error(error);
    if (generation === app.generation) {
      mount(container, h("div", { class: "notice notice-bad" }, t("top.viewFailed", { error: error.message })));
    }
  }
}

/* --------------------------------------------------------------------- */
/*  Общий поток метрик                                                    */
/* --------------------------------------------------------------------- */

function startMetrics() {
  const connect = () => {
    app.ws = openWS("/api/ws/metrics", {
      onOpen: () => {
        app.topbar.live.textContent = `● ${t("top.liveShort")}`;
        app.topbar.live.classList.add("on");
      },
      onClose: () => {
        app.topbar.live.textContent = `○ ${t("top.offline")}`;
        app.topbar.live.classList.remove("on");
      },
      onMessage: (payload) => {
        if (payload.type === "hello") {
          app.metrics.point = payload.point || null;
          app.metrics.state = payload.state || null;
          app.metrics.alerts = payload.alerts || [];
        } else if (payload.type === "metrics") {
          app.metrics.point = payload.point || null;
          app.metrics.state = payload.state || null;
          app.metrics.alerts = payload.alerts || [];
        } else if (payload.type === "ping") {
          return;
        }
        if (payload.alerts) maybeNotify(payload.alerts);
        bus.emit("metrics", app.metrics);
      },
    });
  };
  connect();
}

/* --------------------------------------------------------------------- */
/*  Запуск                                                                */
/* --------------------------------------------------------------------- */

function renderNotes(host) {
  if (!host) return;
  const notes = (app.state && app.state.notes) || [];
  if (!notes.length) {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  const keyOf = (note) => (typeof note === "string" ? note : (note && note.key) || "");
  const issues = notes.filter((note) => keyOf(note) !== "notes.home").length;
  mount(
    host,
    h("summary", { class: "nav-notes-summary", title: t("top.environmentHint") },
      h("span", { class: "nav-notes-title" }, t("top.environment")),
      issues ? h("span", { class: "nav-notes-count" }, String(issues)) : null,
      h("span", { class: "nav-notes-chevron", "aria-hidden": "true" }, "›")),
    h("div", { class: "nav-notes-body" }, notes.map((note) => {
      const text = typeof note === "string" ? t(note) : t(note.key || "", note.params || {});
      return h("p", { class: "note" }, text);
    })),
    h("a", { class: "nav-notes-link", href: "#/settings/engines" }, t("top.toolsSettings"))
  );
}

async function boot() {
  const shell = h("div", { class: "shell" });
  const topbarRefs = buildTopbar();
  app.topbar = topbarRefs;

  const nav = h("nav", { class: "nav" });
  const visible = availableViews();
  for (const group of GROUP_ORDER) {
    const views = visible.filter((view) => view.group === group);
    if (!views.length) continue;
    nav.append(h("div", { class: "nav-group" },
      h("div", { class: "nav-group-label", dataset: { group } }, t(`nav.groups.${group}`)),
      views.map((view) =>
        h("a", {
          class: "nav-item",
          href: `#/${view.id}`,
          dataset: { view: view.id },
          title: t(`nav.${view.id}.desc`),
        }, h("span", { class: "nav-icon" }, view.icon), h("span", { class: "nav-label" }, t(`nav.${view.id}.title`))))));
  }

  const notes = h("details", { class: "nav-notes", hidden: true });
  const view = h("main", { id: "view", class: "view" });
  const backdrop = h("div", { class: "nav-backdrop", onclick: () => toggleMobileNav(false) });
  shell.append(
    h("aside", { class: "sidebar" },
      h("a", { class: "sidebar-brand", href: "#/dashboard", title: t("nav.dashboard.title") },
        "MEGATOOL", h("small", { class: "sidebar-tagline" }, t("top.tagline"))),
      nav, notes),
    backdrop,
    h("div", { class: "content" }, topbarRefs.bar, view)
  );
  document.body.append(shell);

  // Мобильное меню: закрываем по выбору раздела, Esc и переходу на десктоп.
  nav.addEventListener("click", (event) => {
    if (event.target.closest(".nav-item")) toggleMobileNav(false);
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      toggleMobileNav(false);
      if (app.topbar && app.topbar.tools) app.topbar.tools.root.open = false;
    }
  });
  document.addEventListener("click", (event) => {
    const tools = app.topbar && app.topbar.tools && app.topbar.tools.root;
    if (tools && tools.open && !tools.contains(event.target)) tools.open = false;
  });
  window.addEventListener("hashchange", () => toggleMobileNav(false));
  if (window.matchMedia) {
    const desktop = window.matchMedia("(min-width: 901px)");
    const onChange = (event) => {
      if (event.matches) toggleMobileNav(false);
    };
    if (desktop.addEventListener) desktop.addEventListener("change", onChange);
    else if (desktop.addListener) desktop.addListener(onChange);  // старые браузеры
  }

  try {
    const payload = await api.get("/api/settings");
    app.settings = (payload.data && payload.data.ui !== undefined ? payload.data : (payload.data || payload)) || {};
  } catch (error) {
    app.settings = null;
  }
  const ui = (app.settings && app.settings.ui) || {};
  // Язык: настройка сервера важнее локальной копии в браузере.
  if (ui.language && ui.language !== getLang()) {
    setLang(ui.language, { silent: true });
    renderNavLabels();
  }
  applyTheme(ui.theme || app.themePref);
  applyDensity(ui.density);

  refreshTopbar(topbarRefs);
  await refreshState();
  renderNotes(notes);

  window.addEventListener("hashchange", () => {
    if (app.beforeNavigate) {
      const current = app.currentHash;
      let allowed = true;
      try {
        allowed = app.beforeNavigate();
      } catch (error) {
        console.warn("beforeNavigate failed", error);
      }
      if (allowed === false) {
        if (location.hash !== current) {
          // откатываем хэш — раздел остаётся на месте
          history.replaceState(null, "", current);
        }
        return;
      }
    }
    navigate();
  });
  window.addEventListener("megatool:lang", () => renderNavLabels());
  if (!location.hash) location.hash = "#/dashboard";
  await navigate();

  startMetrics();
  setInterval(refreshState, 20000);

  window.addEventListener("keydown", (event) => {
    if (event.key === "F5") return;
    if (event.key === "F1" && !document.querySelector(".overlay")) {
      event.preventDefault();
      openHelp();
      return;
    }
    if (event.key === "t" && event.altKey) toggleTheme();
  });
}

boot();
