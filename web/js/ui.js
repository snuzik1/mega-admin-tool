/* Общие элементы интерфейса: hyperscript, карточки, таблицы, модальные окна,
   уведомления, консоль задач. Никаких фреймворков — только DOM. */

import { api, openWS, session } from "./api.js";
import { locale, t } from "./i18n.js";

/* --------------------------------------------------------------------- */
/*  Права текущего пользователя                                            */
/* --------------------------------------------------------------------- */

/**
 * Проверка права на раздел по данным /api/auth/me (window.megatoolAccess).
 * write=true требует уровень manage; иначе достаточно view.
 * Пока данные не загружены, интерфейс не прячет элементы — сервер всё равно
 * проверит право на каждом запросе.
 */
export function can(section, write = false) {
  const access = window.megatoolAccess;
  if (!access) return true;
  if (access.mode === "local" || access.mode === "master") return true;
  const level = (access.permissions || {})[section] || "none";
  return write ? level === "manage" : (level === "view" || level === "manage");
}

/* --------------------------------------------------------------------- */
/*  Базовое                                                                */
/* --------------------------------------------------------------------- */

export function h(tag, props, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class" || key === "className") element.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : value;
    else if (key === "style" && typeof value === "object") Object.assign(element.style, value);
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key === "html") element.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") element.value = value;
    else if (key === "checked" || key === "disabled" || key === "selected") element[key] = Boolean(value);
    else element.setAttribute(key, value === true ? "" : value);
  }
  append(element, children);
  return element;
}

export function append(element, children) {
  for (const child of children.flat(3)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function mount(root, ...children) {
  clear(root);
  append(root, children);
  return root;
}

export function icon(glyph, title) {
  return h("span", { class: "ico", title: title || "", "aria-hidden": "true" }, glyph);
}

/* --------------------------------------------------------------------- */
/*  Форматирование                                                         */
/* --------------------------------------------------------------------- */

export function fmtBytes(value, suffix = "") {
  if (value === null || value === undefined || !isFinite(value)) return t("common.dash");
  const units = t("common.units");
  let number = Math.abs(value);
  let unit = 0;
  while (number >= 1024 && unit < units.length - 1) {
    number /= 1024;
    unit += 1;
  }
  const digits = number < 10 && unit > 0 ? 1 : number < 100 && unit > 0 ? 1 : 0;
  return `${value < 0 ? "-" : ""}${number.toFixed(digits)} ${units[unit]}${suffix}`;
}

export function fmtNumber(value, digits = 1) {
  if (value === null || value === undefined || !isFinite(value)) return t("common.dash");
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString(locale());
  return Number(value.toFixed(digits)).toString();
}

export function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || !isFinite(value)) return t("common.dash");
  return `${Number(value.toFixed(digits))}%`;
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return t("common.dash");
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days) return t("common.duration.days", { d: days, h: hours });
  if (hours) return t("common.duration.hours", { h: hours, m: minutes });
  if (minutes) return t("common.duration.minutes", { m: minutes, s: secs });
  return t("common.duration.seconds", { s: secs });
}

export function fmtTime(iso) {
  if (!iso) return t("common.dash");
  const date = new Date(iso);
  if (isNaN(date.getTime())) return String(iso);
  try {
    return date.toLocaleString(locale(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (error) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(date.getDate())}.${pad(date.getMonth() + 1)} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }
}

export function timeAgo(iso) {
  if (!iso) return t("common.dash");
  const stamp = new Date(iso).getTime();
  if (isNaN(stamp)) return String(iso);
  const diff = (Date.now() - stamp) / 1000;
  if (diff < 5) return t("common.ago.justNow");
  if (diff < 0) return t("common.ago.inFuture");
  if (diff < 60) return t("common.ago.seconds", { n: Math.floor(diff) });
  if (diff < 3600) return t("common.ago.minutes", { n: Math.floor(diff / 60) });
  if (diff < 86400) return t("common.ago.hours", { n: Math.floor(diff / 3600) });
  return t("common.ago.days", { n: Math.floor(diff / 86400) });
}

/* --------------------------------------------------------------------- */
/*  Карточки, значки, полосы                                               */
/* --------------------------------------------------------------------- */

export function card(title, body, options = {}) {
  const head = title
    ? h("div", { class: "card-head" }, h("h3", {}, title), options.extra || null)
    : null;
  const element = h("section", { class: ["card", options.class].filter(Boolean).join(" ") }, head, h("div", { class: "card-body" }, body));
  if (options.id) element.id = options.id;
  return element;
}

export function badge(text, kind = "neutral", title = "") {
  return h("span", { class: `badge badge-${kind}`, title }, text);
}

const STATE_KINDS = {
  running: "ok",
  success: "ok",
  healthy: "ok",
  ready: "ok",
  paused: "warn",
  restarting: "warn",
  pending: "warn",
  starting: "warn",
  created: "muted",
  configured: "muted",
  exited: "muted",
  stopped: "muted",
  failed: "bad",
  error: "bad",
  unhealthy: "bad",
  cancelled: "muted",
  warning: "warn",
  critical: "bad",
  info: "muted",
  true: "ok",
  false: "bad",
};

export function stateBadge(value, label = null) {
  const key = String(value ?? "").toLowerCase();
  return badge(label ?? value ?? "—", STATE_KINDS[key] || "neutral");
}

export function bar(ratio, { kind = "", label = "" } = {}) {
  const percent = Math.max(0, Math.min(100, (ratio || 0) * 100));
  return h(
    "div",
    { class: `bar ${kind}`, title: label || `${percent.toFixed(1)}%` },
    h("div", { class: "bar-fill", style: { width: `${percent}%` } })
  );
}

export function kvRows(pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.map(([key, value]) =>
      h("div", { class: "kv-row" }, h("dt", {}, key), h("dd", {}, value === null || value === undefined || value === "" ? "—" : value))
    )
  );
}

export function chip(text, options = {}) {
  return h("button", { class: `chip ${options.active ? "active" : ""}`, onclick: options.onclick, type: "button" }, text);
}

export function toolbar(...children) {
  return h("div", { class: "toolbar" }, children);
}

export function button(label, onclick, options = {}) {
  return h(
    "button",
    {
      type: "button",
      class: ["btn", options.kind ? `btn-${options.kind}` : "", options.size ? `btn-${options.size}` : "", options.class].filter(Boolean).join(" "),
      onclick,
      disabled: options.disabled,
      title: options.title || "",
    },
    options.icon ? icon(options.icon) : null,
    label
  );
}

export function input(props = {}) {
  return h("input", Object.assign({ type: "text", class: "input" }, props));
}

export function select(options, props = {}) {
  const element = h("select", Object.assign({ class: "input" }, props));
  for (const item of options) {
    const option = h("option", { value: item.value }, item.label);
    if (props.value !== undefined && String(item.value) === String(props.value)) option.selected = true;
    element.append(option);
  }
  return element;
}

export function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "field-hint" }, hint) : null);
}

export function empty(text, options = {}) {
  return h("div", { class: "empty" }, h("div", { class: "empty-mark" }, options.mark || "▢"), h("p", {}, text), options.hint ? h("p", { class: "muted" }, options.hint) : null, options.extra || null);
}

export function spinner(text = null) {
  return h("div", { class: "loading" }, h("span", { class: "spinner" }), text || t("common.loading"));
}

export function notice(text, kind = "warn") {
  return h("div", { class: `notice notice-${kind}` }, text);
}

/* --------------------------------------------------------------------- */
/*  Таблица                                                               */
/* --------------------------------------------------------------------- */

/**
 * table({ columns: [{key,label,render(row),width,align,sortable,sortValue}], rows, empty, onRow, class })
 * render(row, index) возвращает узел/строку; при отсутствии render выводится row[key].
 */
export function table(config) {
  const state = { sortKey: null, direction: -1 };
  const wrapper = h("div", { class: ["table-wrap", config.class].filter(Boolean).join(" ") });
  const body = h("tbody");

  const sortRows = (rows) => {
    if (!state.sortKey) return rows;
    const column = config.columns.find((item) => item.key === state.sortKey);
    if (!column) return rows;
    const value = (row) => (column.sortValue ? column.sortValue(row) : row[column.key]);
    return [...rows].sort((a, b) => {
      const left = value(a);
      const right = value(b);
      if (left === right) return 0;
      if (left === null || left === undefined) return 1;
      if (right === null || right === undefined) return -1;
      if (typeof left === "number" && typeof right === "number") return (left - right) * state.direction;
      return String(left).localeCompare(String(right), locale()) * state.direction;
    });
  };

  const renderHead = () => {
    const head = h("thead");
    const row = h("tr");
    for (const column of config.columns) {
      const clickable = column.sortable !== false && config.columns.length > 1;
      const cell = h(
        "th",
        {
          class: [column.align ? `ta-${column.align}` : "", clickable ? "sortable" : "", state.sortKey === column.key ? "sorted" : ""].filter(Boolean).join(" "),
          style: column.width ? { width: column.width } : undefined,
          onclick: clickable
            ? () => {
                if (state.sortKey === column.key) state.direction *= -1;
                else {
                  state.sortKey = column.key;
                  state.direction = -1;
                }
                render();
              }
            : undefined,
        },
        column.label || column.key,
        state.sortKey === column.key ? (state.direction === -1 ? " ↓" : " ↑") : ""
      );
      row.append(cell);
    }
    head.append(row);
    wrapper.querySelector("table").replaceChild(head, wrapper.querySelector("thead"));
  };

  const render = () => {
    const rows = sortRows(config.rows || []);
    clear(body);
    if (!rows.length) {
      body.append(
        h("tr", {}, h("td", { colspan: config.columns.length, class: "table-empty" }, config.empty || t("common.defaultEmpty")))
      );
    } else {
      rows.forEach((row, index) => {
        const tr = h("tr", { class: config.onRow ? "clickable" : "" });
        for (const column of config.columns) {
          let content;
          if (column.render) content = column.render(row, index);
          else content = row[column.key] === null || row[column.key] === undefined ? "—" : row[column.key];
          tr.append(h("td", { class: column.align ? `ta-${column.align}` : "", title: column.title ? column.title(row) : "" }, content));
        }
        if (config.onRow) tr.addEventListener("click", (event) => {
          if (event.target.closest("button, a, input, select")) return;
          config.onRow(row, index);
        });
        body.append(tr);
      });
    }
    renderHead();
  };

  wrapper.append(h("table", { class: "table" }, h("thead"), body));
  render();
  wrapper.update = (rows) => {
    config.rows = rows;
    render();
  };
  return wrapper;
}

/* --------------------------------------------------------------------- */
/*  Модальные окна и уведомления                                           */
/* --------------------------------------------------------------------- */

export function modal({ title, body, actions = [], width = 520, onClose = null, key = null }) {
  const overlay = h("div", { class: "overlay" });
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  const head = h("div", { class: "modal-head" }, h("h3", {}, title),
    h("button", { class: "modal-close", type: "button", onclick: close, title: t("common.modal.close") }, "✕"));
  const footer = actions.length
    ? h("div", { class: "modal-foot" }, actions.filter(Boolean).map((action) =>
        h("button", {
          type: "button",
          class: `btn ${action.kind ? `btn-${action.kind}` : ""}`,
          onclick: () => action.onClick ? action.onClick(close) : close(),
          disabled: action.disabled,
        }, action.label)))
    : null;
  const dialog = h("div", { class: "modal", style: { width: `${width}px` } }, head, h("div", { class: "modal-body" }, body), footer);
  overlay.append(dialog);
  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) close();
  });
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
  const focusable = dialog.querySelector("input, textarea, select, button");
  if (focusable) setTimeout(() => focusable.focus(), 30);
  return { close, dialog, overlay };
}

export function confirmDialog(message, { title = null, okLabel = null, danger = true } = {}) {
  return new Promise((resolve) => {
    let decided = false;
    modal({
      title: title || t("common.modal.confirmTitle"),
      body: h("p", { class: "confirm-text" }, message),
      width: 420,
      actions: [
        { label: t("common.buttons.cancel"), onClick: (close) => { decided = true; close(); resolve(false); } },
        { label: okLabel || t("common.buttons.confirm"), kind: danger ? "danger" : "primary", onClick: (close) => { decided = true; close(); resolve(true); } },
      ],
      onClose: () => { if (!decided) resolve(false); },
    });
  });
}

export function promptDialog(message, { title = null, value = "", placeholder = "" } = {}) {
  return new Promise((resolve) => {
    const control = input({ value, placeholder });
    let decided = false;
    modal({
      title: title || t("common.modal.promptTitle"),
      body: h("div", {}, h("p", {}, message), control),
      width: 420,
      actions: [
        { label: t("common.buttons.cancel"), onClick: (close) => { decided = true; close(); resolve(null); } },
        { label: t("common.buttons.ok"), kind: "primary", onClick: (close) => { decided = true; close(); resolve(control.value); } },
      ],
      onClose: () => { if (!decided) resolve(null); },
    });
  });
}

export function toast(message, kind = "info", timeout = 4200) {
  let host = document.querySelector(".toasts");
  if (!host) {
    host = h("div", { class: "toasts" });
    document.body.append(host);
  }
  const element = h("div", { class: `toast toast-${kind}` }, h("span", { class: "toast-mark" }, kind === "error" ? "✕" : kind === "success" ? "✓" : "•"), h("span", {}, message));
  host.append(element);
  const remove = () => {
    element.classList.add("leaving");
    setTimeout(() => element.remove(), 220);
  };
  element.addEventListener("click", remove);
  setTimeout(remove, timeout);
  return element;
}

/** Обёртка над запросом: ошибку показываем тостом, успех — по желанию. */
export async function guard(task, { success = "", quiet = false } = {}) {
  try {
    const result = await task();
    if (success && result && result.ok !== false) toast(success, "success");
    return result;
  } catch (error) {
    if (error && error.status === 401) {
      toast(t("common.toast.tokenRequired"), "error", 8000);
      askToken();
    } else if (!quiet) {
      toast(error && error.message ? error.message : t("common.toast.requestFailed"), "error");
    }
    return null;
  }
}

let loginPromptShown = false;

/**
 * Вход: логин/пароль пользователя из раздела «Доступ» либо мастер-токен.
 * Вызывается при 401 и при старте приложения, если доступ закрыт.
 */
export function askToken() {
  if (loginPromptShown) return;
  loginPromptShown = true;

  const username = input({ placeholder: "operator", autocomplete: "username" });
  const password = input({ type: "password", autocomplete: "current-password" });
  const token = input({ type: "password", placeholder: t("common.token.placeholder") });
  const error = h("div", { class: "notice notice-bad", style: { display: "none" } });
  const userBox = h("div", { class: "col" },
    h("p", {}, t("common.auth.body")),
    field(t("common.auth.username"), username),
    field(t("common.auth.password"), password));
  const tokenBox = h("div", { class: "col", style: { display: "none" } },
    h("p", {}, t("common.token.body")),
    field(t("common.auth.tokenMode"), token, t("common.auth.tokenHint")));
  let tokenMode = false;

  const switchButton = button(t("common.auth.tokenMode"), () => {
    tokenMode = !tokenMode;
    userBox.style.display = tokenMode ? "none" : "";
    tokenBox.style.display = tokenMode ? "" : "none";
    switchButton.textContent = tokenMode ? t("common.auth.userMode") : t("common.auth.tokenMode");
    error.style.display = "none";
  }, { size: "sm", kind: "ghost" });

  const showError = (message) => {
    error.textContent = message;
    error.style.display = "";
  };

  const finish = (message = "") => {
    loginPromptShown = false;
    if (message) toast(message, "success");
    setTimeout(() => window.location.reload(), message ? 500 : 0);
  };

  modal({
    title: t("common.auth.title"),
    body: h("div", { class: "col" }, userBox, tokenBox, switchButton, error),
    width: 440,
    onClose: () => { loginPromptShown = false; },
    actions: [
      { label: t("common.buttons.cancel") },
      {
        label: t("common.auth.login"),
        kind: "primary",
        onClick: async (close) => {
          error.style.display = "none";
          if (tokenMode) {
            const value = token.value.trim();
            if (!value) {
              showError(t("common.auth.needToken"));
              return false;
            }
            session.token = value;
            try {
              const result = await api.get("/api/auth/me");
              if (!result || result.ok === false) throw new Error(t("common.auth.failed"));
              close();
              finish(t("common.token.saved"));
            } catch (problem) {
              session.token = "";
              showError(problem && problem.message ? problem.message : t("common.auth.failed"));
            }
            return false;
          }
          const login = username.value.trim();
          const secret = password.value;
          if (!login) {
            showError(t("common.auth.needUsername"));
            return false;
          }
          if (!secret) {
            showError(t("common.auth.needPassword"));
            return false;
          }
          try {
            const result = await api.post("/api/auth/login", { username: login, password: secret });
            if (!result || result.ok === false || !result.data || !result.data.token) {
              throw new Error(result && result.error ? result.error : t("common.auth.failed"));
            }
            session.token = result.data.token;
            close();
            finish(t("common.auth.signedIn"));
          } catch (problem) {
            showError(problem && problem.message ? problem.message : t("common.auth.failed"));
          }
          return false;
        },
      },
    ],
  });
}

/* --------------------------------------------------------------------- */
/*  Первый локальный администратор и смена пароля MegaTool                 */
/* --------------------------------------------------------------------- */

let setupPromptShown = false;
let passwordPromptShown = false;

export async function getSetupStatus() {
  try {
    const result = await api.get("/api/auth/setup-status");
    return result && result.ok !== false ? (result.data || null) : null;
  } catch (error) {
    return null;
  }
}

export async function showSetupIfRequired() {
  if (setupPromptShown) return true;
  const status = await getSetupStatus();
  if (!status || !status.setup_required) return false;
  if (!status.setup_available) {
    toast(t("common.setup.unavailable"), "warn", 8000);
    return false;
  }
  if (!status.auth_enabled && sessionStorage.getItem("megatool.setup.dismissed") === "1") {
    return false;
  }
  askSetup(status);
  return true;
}

export async function openSetupWizard() {
  const status = await getSetupStatus();
  if (!status) {
    toast(t("common.toast.requestFailed"), "error");
    return;
  }
  if (status.users) {
    toast(t("common.setup.exists"), "info");
    return;
  }
  if (!status.setup_available) {
    toast(t("common.setup.unavailable"), "warn", 8000);
    return;
  }
  askSetup(status);
}

function askSetup(status) {
  if (setupPromptShown) return;
  setupPromptShown = true;
  let done = false;
  const rawLogin = String(status.os_user || "").toLowerCase();
  const suggested = /^[a-z0-9_.-]{2,32}$/.test(rawLogin) ? rawLogin : "admin";
  const login = input({
    value: "", placeholder: t("common.setup.loginPlaceholder", { name: suggested }),
    autocomplete: "username", maxlength: "32",
  });
  const password = input({ type: "password", autocomplete: "new-password", maxlength: "200" });
  const repeat = input({ type: "password", autocomplete: "new-password", maxlength: "200" });
  const error = h("div", { class: "notice notice-bad", style: { display: "none" } });
  const showError = (message) => {
    error.textContent = message;
    error.style.display = "";
  };
  const body = h("div", { class: "col" },
    h("p", {}, t("common.setup.body")),
    h("div", { class: "notice" },
      h("strong", {}, `${t("common.setup.account")}: ${status.os_user || "?"}`),
      h("div", { class: "small muted" }, t("common.setup.root"))),
    field(t("common.setup.login"), login, t("common.setup.loginHint")),
    h("div", { class: "grid cards-2" },
      field(t("common.setup.password"), password),
      field(t("common.setup.repeat"), repeat)),
    h("p", { class: "small muted" }, t("common.setup.hint")),
    error);

  modal({
    title: t("common.setup.title"),
    width: 540,
    body,
    onClose: () => {
      setupPromptShown = false;
      if (done) return;
      if (status.auth_enabled) {
        setTimeout(() => askToken(), 0);
      } else {
        sessionStorage.setItem("megatool.setup.dismissed", "1");
        setTimeout(() => window.location.reload(), 0);
      }
    },
    actions: [
      { label: t("common.buttons.later") },
      {
        label: t("common.setup.create"),
        kind: "primary",
        onClick: async (close) => {
          error.style.display = "none";
          if (!login.value.trim()) {
            showError(t("common.setup.needLogin"));
            return false;
          }
          if (!password.value) {
            showError(t("common.auth.needPassword"));
            return false;
          }
          if (password.value !== repeat.value) {
            showError(t("common.setup.mismatch"));
            return false;
          }
          try {
            const result = await api.post("/api/auth/setup", {
              username: login.value.trim(),
              password: password.value,
            });
            if (!result || result.ok === false || !result.data || !result.data.token) {
              throw new Error(result && result.error ? result.error : t("common.auth.failed"));
            }
            done = true;
            session.token = result.data.token;
            sessionStorage.removeItem("megatool.setup.dismissed");
            close();
            toast(t("common.setup.done"), "success");
            setTimeout(() => window.location.reload(), 500);
          } catch (problem) {
            showError(problem && problem.message ? problem.message : t("common.auth.failed"));
          }
          return false;
        },
      },
    ],
  });
}

export function askPasswordChange() {
  if (passwordPromptShown) return;
  passwordPromptShown = true;
  const current = input({ type: "password", autocomplete: "current-password", maxlength: "200" });
  const next = input({ type: "password", autocomplete: "new-password", maxlength: "200" });
  const repeat = input({ type: "password", autocomplete: "new-password", maxlength: "200" });
  const error = h("div", { class: "notice notice-bad", style: { display: "none" } });
  const showError = (message) => {
    error.textContent = message;
    error.style.display = "";
  };
  const body = h("div", { class: "col" },
    h("p", {}, t("common.password.body")),
    field(t("common.password.current"), current),
    h("div", { class: "grid cards-2" },
      field(t("common.password.new"), next),
      field(t("common.password.repeat"), repeat)),
    h("p", { class: "small muted" }, t("common.password.hint")),
    error);

  modal({
    title: t("common.password.title"),
    width: 540,
    body,
    onClose: () => { passwordPromptShown = false; },
    actions: [
      { label: t("common.buttons.cancel") },
      {
        label: t("common.password.change"),
        kind: "primary",
        onClick: async (close) => {
          error.style.display = "none";
          if (!current.value) {
            showError(t("common.password.needCurrent"));
            return false;
          }
          if (!next.value) {
            showError(t("common.password.needNew"));
            return false;
          }
          if (next.value !== repeat.value) {
            showError(t("common.password.mismatch"));
            return false;
          }
          try {
            const result = await api.post("/api/auth/password", {
              current_password: current.value,
              new_password: next.value,
            });
            if (!result || result.ok === false || !result.data || !result.data.token) {
              throw new Error(result && result.error ? result.error : t("common.auth.failed"));
            }
            session.token = result.data.token;
            close();
            toast(t("common.password.changed"), "success");
          } catch (problem) {
            showError(problem && problem.message ? problem.message : t("common.auth.failed"));
          }
          return false;
        },
      },
    ],
  });
}

/* --------------------------------------------------------------------- */
/*  Консоль задачи (живой лог по WebSocket)                                */
/* --------------------------------------------------------------------- */

export function consoleView({ height = 320 } = {}) {
  const output = h("pre", { class: "console", style: { height: `${height}px` } });
  const status = h("div", { class: "console-status" }, t("common.console.waiting"));
  const element = h("div", { class: "console-box" }, h("div", { class: "console-head" }, status), output);
  let socket = null;
  let current = null;

  const write = (text, stream = "stdout") => {
    const line = document.createElement("div");
    line.className = `console-line console-${stream}`;
    line.textContent = text;
    output.append(line);
    while (output.childNodes.length > 4000) output.removeChild(output.firstChild);
    output.scrollTop = output.scrollHeight;
  };

  const setStatus = (text) => {
    status.textContent = text;
  };

  const attach = (jobId) => {
    if (socket) socket.close();
    current = jobId;
    clear(output);
    setStatus(t("common.console.attaching", { id: jobId }));
    socket = openWS(`/api/ws/jobs/${jobId}`, {
      onMessage: (payload) => {
        if (payload.type === "line") {
          write(payload.text, payload.stream === "stderr" ? "stderr" : payload.stream === "system" || payload.stream === "error" ? "system" : "stdout");
        } else if (payload.type === "job" || payload.type === "status") {
          const job = payload.job || {};
          const code = job.exit_code !== null && job.exit_code !== undefined ? t("common.console.exitCode", { code: job.exit_code }) : "";
          const duration = job.duration ? t("common.console.duration", { seconds: job.duration }) : "";
          setStatus(t("common.console.status", { id: job.id, status: job.status }) + code + duration);
        } else if (payload.type === "error") {
          setStatus(payload.error || t("common.console.error"));
        }
      },
      onClose: () => setStatus(t("common.console.streamClosed")),
    });
    return socket;
  };

  const destroy = () => {
    if (socket) socket.close();
  };

  return { element, write, attach, destroy, setStatus, get jobId() { return current; } };
}

/** Модальное окно с живым логом задачи. */
export function jobModal(job, { title = null } = {}) {
  const view = consoleView({ height: 420 });
  const instance = modal({
    title: title || t("common.job.title", { title: job.title || job.id }),
    body: view.element,
    width: 860,
    onClose: () => view.destroy(),
    actions: [
      {
        label: t("common.job.cancel"),
        kind: "danger",
        onClick: async () => {
          await guard(() => api.post(`/api/jobs/${job.id}/cancel`), { success: t("common.console.cancelRequested") });
        },
      },
      { label: t("common.buttons.close") },
    ],
  });
  view.attach(job.id);
  return instance;
}

/* --------------------------------------------------------------------- */
/*  Вкладки                                                                */
/* --------------------------------------------------------------------- */

export function tabs(items, { active = null, onChange = null } = {}) {
  const element = h("div", { class: "tabs" });
  let current = active || (items[0] && items[0].id);
  const buttons = items.map((item) =>
    h("button", {
      type: "button",
      class: `tab ${item.id === current ? "active" : ""}`,
      onclick: () => {
        current = item.id;
        buttons.forEach((button, index) => button.classList.toggle("active", items[index].id === current));
        if (onChange) onChange(current);
      },
    }, item.label, item.count !== undefined ? h("span", { class: "tab-count" }, item.count) : null)
  );
  append(element, buttons);
  element.setActive = (id) => {
    current = id;
    buttons.forEach((button, index) => button.classList.toggle("active", items[index].id === id));
  };
  return element;
}

/** Периодический опрос. Возвращает функцию остановки. */
export function poller(intervalMs, task, { immediate = true } = {}) {
  let busy = false;
  const run = async () => {
    if (busy) return;
    busy = true;
    try {
      await task();
    } finally {
      busy = false;
    }
  };
  if (immediate) run();
  const timer = setInterval(run, intervalMs);
  return () => clearInterval(timer);
}

/* Шина событий: метрики из общего WebSocket и смена темы. */
export const bus = {
  handlers: new Map(),
  on(event, handler) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event).add(handler);
    return () => this.off(event, handler);
  },
  off(event, handler) {
    const set = this.handlers.get(event);
    if (set) set.delete(handler);
  },
  emit(event, payload) {
    const set = this.handlers.get(event);
    if (!set) return;
    for (const handler of [...set]) {
      try {
        handler(payload);
      } catch (error) {
        console.error("bus handler failed", event, error);
      }
    }
  },
};
