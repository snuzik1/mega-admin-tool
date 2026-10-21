/* Каркас интерфейса: боковое меню, верхняя панель, маршрутизация по хэшу,
   общий WebSocket метрик, тема, язык и системные уведомления. */

import { api, openWS, session } from "./api.js";
import { getLang, LANG_LABELS, setLang, SUPPORTED_LANGS, t } from "./i18n.js";
import {
  askPasswordChange, askToken, bus, button, can, fmtDuration, h, mount,
  showSetupIfRequired, timeAgo, toast,
} from "./ui.js";

/* Разделы приложения. Подписи и подсказки — из словарей (nav.<id>). */
const VIEWS = [
  { id: "dashboard", icon: "▤", module: "./views/dashboard.js", group: "monitoring" },
  { id: "checks", icon: "◎", module: "./views/checks.js", group: "monitoring" },
  { id: "hosts", icon: "▥", module: "./views/hosts.js", group: "monitoring" },
  { id: "containers", icon: "▣", module: "./views/containers.js", group: "infrastructure" },
  { id: "balancer", icon: "⇄", module: "./views/balancer.js", group: "infrastructure" },
  { id: "catalog", icon: "▦", module: "./views/catalog.js", group: "infrastructure" },
  { id: "bundles", icon: "▧", module: "./views/bundles.js", group: "automation" },
  { id: "configs", icon: "✎", module: "./views/configs.js", group: "automation" },
  { id: "ansible", icon: "◆", module: "./views/ansible.js", group: "automation" },
  { id: "servers", icon: "⌁", module: "./views/servers.js", group: "automation" },
  { id: "k8s", icon: "☸", module: "./views/k8s.js", group: "automation" },
  { id: "changes", icon: "◔", module: "./views/changes.js", group: "monitoring" },
  { id: "jobs", icon: "▶", module: "./views/jobs.js", group: "monitoring" },
  { id: "access", icon: "⚿", module: "./views/access.js", group: "app" },
  { id: "settings", icon: "⚙", module: "./views/settings.js", group: "app" },
];

const GROUP_ORDER = ["monitoring", "infrastructure", "automation", "app"];

const app = {
  view: null,
  cleanup: null,
  state: null,
  settings: null,
  me: null,
  access: null,
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

function buildTopbar() {
  const host = h("span", { class: "top-host" }, "…");
  const version = h("span", { class: "top-version muted" }, "");
  const uptime = h("span", { class: "top-uptime" }, "—");
  const alerts = h("button", { class: "top-alert", type: "button", onclick: () => (location.hash = "#/dashboard/alerts") }, "▲ 0");
  const engines = h("span", { class: "top-flags" });
  const live = h("span", { class: "top-live" }, "○");
  const langButton = button(getLang().toUpperCase(), () => toggleLang(), { class: "top-lang", title: t("top.lang") });
  const themeButton = button("", () => toggleTheme(), { class: "top-theme", title: t("top.theme") });
  const account = h("button", {
    type: "button", class: "top-user", title: t("top.user"),
    onclick: () => askPasswordChange(),
  });
  const logoutButton = button("⎋", () => signOut(), { class: "top-logout", title: t("top.logout") });
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
    h("div", { class: "top-left" }, menuButton, h("strong", { class: "brand" }, "MEGATOOL"), version),
    h("div", { class: "top-mid" }, host, h("span", { class: "sep" }, "•"), uptime),
    h("div", { class: "top-right" }, engines, alerts, live, account, logoutButton,
      langButton, themeButton)
  );
  return {
    bar, host, version, uptime, alerts, engines, live, account, logoutButton,
    langButton, themeButton, menuButton,
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

function flag(name, available, title) {
  return h("span", { class: `flag ${available ? "on" : "off"}`, title }, name);
}

function refreshAccount(refs) {
  if (!refs || !refs.account) return;
  const me = app.me || {};
  const mode = me.mode || "local";
  const visible = mode !== "local";
  refs.account.style.display = visible ? "" : "none";
  if (refs.logoutButton) refs.logoutButton.style.display = visible ? "" : "none";
  if (!visible) return;
  refs.account.disabled = mode !== "user";
  if (mode === "master") {
    refs.account.textContent = t("top.master");
    refs.account.title = t("top.master");
  } else {
    const user = me.user || {};
    const local = user.local_account ? ` · ${t("common.setup.account")}: ${user.local_account}` : "";
    const owner = user.owner ? ` · ${t("top.owner")}` : "";
    refs.account.textContent = user.display_name || user.username || "user";
    refs.account.title = `${t("top.user")}: @${user.username || ""}${user.preset_name ? ` · ${user.preset_name}` : ""}${owner}${local} · ${t("top.password")}`;
  }
}

async function signOut() {
  try {
    await api.post("/api/auth/logout");
  } catch (error) {
    /* мастер-токен или выключенная проверка — всё равно очищаем браузер */
  }
  session.token = "";
  toast(t("common.auth.signedOut"), "success");
  setTimeout(() => window.location.reload(), 300);
}

function refreshTopbar(refs) {
  if (!refs) return;
  refreshAccount(refs);
  const state = app.state;
  if (state) {
    const info = state.app || {};
    refs.version.textContent = `v${info.version || ""}`;
    app.uptimeBase = app.uptimeBase || { at: Date.now(), uptime: info.uptime || 0 };
    const seconds = app.uptimeBase.uptime + (Date.now() - app.uptimeBase.at) / 1000;
    refs.uptime.textContent = t("top.uptime", { duration: fmtDuration(seconds) });
    const alerts = state.alerts || [];
    const critical = alerts.filter((item) => item.severity === "critical").length;
    refs.alerts.textContent = `▲ ${alerts.length}`;
    refs.alerts.classList.toggle("has-critical", critical > 0);
    refs.alerts.title = alerts.length
      ? alerts.slice(0, 8).map((item) => `${item.severity}: ${item.title}`).join("\n")
      : t("top.noAlerts");
    const engines = state.engines || {};
    const active = engines.active;
    mount(
      refs.engines,
      flag("D", Boolean(active === "docker"), "Docker"),
      flag("P", Boolean(active === "podman" || (engines.engines && engines.engines.podman && engines.engines.podman.available)), "Podman"),
      flag("K", Boolean(state.k8s && state.k8s.available), "kubectl"),
      flag("A", Boolean(state.ansible && state.ansible.available), "Ansible")
    );
    const monitor = state.monitor || {};
    refs.host.textContent = monitor.hostname || state.hostname || "";
    refs.host.title = monitor.last_point_iso ? t("top.lastSample", { ago: timeAgo(monitor.last_point_iso) }) : "";
  }
  if (refs.live) {
    refs.live.title = t("top.live");
    if (!refs.live.classList.contains("on")) refs.live.textContent = `○ ${t("top.offline")}`;
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
  if (!can("settings", true)) return;
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
  if (persist && can("settings", true)) {
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
  return VIEWS.filter((view) => {
    // Настройки и управление доступом — только с правом управления.
    if (view.id === "settings" || view.id === "access") return can(view.id, true);
    return can(view.id, false);
  });
}

async function navigate() {
  const { id, args } = parseHash();
  const views = availableViews();
  if (!views.length) {
    const host = document.getElementById("view");
    if (host) mount(host, h("div", { class: "view-body" },
      h("div", { class: "notice notice-bad" }, t("top.noAccess"))));
    document.title = "MEGATOOL";
    return;
  }
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
  document.querySelectorAll(".nav-item").forEach((element) =>
    element.classList.toggle("active", element.dataset.view === view.id));
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
  const notes = (app.state && app.state.notes) || [];
  mount(host, notes.map((note) => {
    const text = typeof note === "string" ? t(note) : t(note.key || "", note.params || {});
    return h("p", { class: "note" }, text);
  }));
}

async function loadAccess() {
  // Если включён вход, но пользователей ещё нет, сначала предлагаем создать
  // первого локального администратора. Это единственный публичный сценарий.
  if (await showSetupIfRequired()) return false;
  try {
    const result = await api.get("/api/auth/me");
    app.me = result.data || {};
    app.access = app.me;
    window.megatoolAccess = app.me;
    return true;
  } catch (error) {
    app.me = null;
    app.access = null;
    window.megatoolAccess = null;
    if (error && error.status === 401) {
      askToken();
      return false;
    }
    return true;
  }
}

async function boot() {
  // Сначала узнаём пользователя и его права: от этого зависит меню.
  if (!(await loadAccess())) return;

  const shell = h("div", { class: "shell" });
  const topbarRefs = buildTopbar();
  app.topbar = topbarRefs;
  if (!can("dashboard", false)) {
    // Сводка состояния и live-поток относятся только к дашборду.
    const mid = topbarRefs.bar.querySelector(".top-mid");
    for (const element of [mid, topbarRefs.alerts, topbarRefs.live]) {
      if (element) element.style.display = "none";
    }
  }

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

  const notes = h("div", { class: "nav-notes" });
  const view = h("main", { id: "view", class: "view" });
  const backdrop = h("div", { class: "nav-backdrop", onclick: () => toggleMobileNav(false) });
  shell.append(
    h("aside", { class: "sidebar" },
      h("div", { class: "sidebar-brand" }, "MEGATOOL", h("small", { class: "sidebar-tagline" }, t("top.tagline"))),
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
    if (event.key === "Escape") toggleMobileNav(false);
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

  if (can("settings", false)) {
    try {
      const payload = await api.get("/api/settings");
      app.settings = (payload.data && payload.data.ui !== undefined ? payload.data : (payload.data || payload)) || {};
    } catch (error) {
      app.settings = null;
      if (error.status === 401) toast(t("common.toast.tokenRequired"), "error", 8000);
    }
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
  if (can("dashboard", false)) {
    await refreshState();
    renderNotes(notes);
  } else {
    renderNotes(notes);
  }

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

  if (can("dashboard", false)) {
    startMetrics();
    setInterval(refreshState, 20000);
  }

  window.addEventListener("keydown", (event) => {
    if (event.key === "F5") return;
    if (event.key === "t" && event.altKey) toggleTheme();
  });
}

boot();
