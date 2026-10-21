/* Доступность: HTTP-адреса, TCP-порты и TLS-сертификаты.
   Проверки выполняет сервер в фоне, здесь — управление и история. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, can, card, confirmDialog, empty, field, fmtNumber, fmtTime, guard, h, input,
  modal, mount, poller, select, table, toast, toolbar,
} from "../ui.js";

const TYPES = [
  { value: "http", key: "checks.type.http" },
  { value: "tcp", key: "checks.type.tcp" },
  { value: "tls", key: "checks.type.tls" },
];

const STATE_KIND = { up: "ok", warn: "warn", down: "bad", unknown: "muted", disabled: "muted" };

const configOf = (row) => {
  const item = {
    id: row.id,
    name: row.name,
    type: row.type,
    interval_sec: Number(row.interval_sec) || 60,
    timeout_sec: Number(row.timeout_sec) || 5,
    enabled: row.enabled !== false,
  };
  if (row.type === "http") {
    item.url = row.url;
    item.expected_status = Number(row.expected_status) || 200;
    item.keyword = row.keyword || "";
    item.verify_tls = row.verify_tls !== false;
  } else {
    item.host = row.host;
    item.port = Number(row.port) || (row.type === "tls" ? 443 : 80);
    item.verify_tls = row.verify_tls !== false;
    if (row.type === "tls") item.warn_days = Number(row.warn_days) || 30;
  }
  return item;
};

function stateBadge(state) {
  return badge(t(`checks.state.${state || "unknown"}`), STATE_KIND[state] || "neutral");
}

function historyStrip(row) {
  const points = (row.history || []).slice(-30);
  if (!points.length) return h("span", { class: "muted small" }, t("checks.never"));
  return h("div", { class: "check-strip" }, points.map((point) =>
    h("span", {
      class: `check-block ${point.ok ? (point.warn ? "warn" : "ok") : "bad"}`,
      title: `${fmtTime(point.ts)} · ${point.ok ? t("checks.state.up") : t("checks.state.down")}${point.latency_ms !== undefined ? ` · ${fmtNumber(point.latency_ms, 0)} ${t("checks.ms")}` : ""}`,
    })));
}

function downloadReport(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function reportStamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}

export async function render(root, { args = [] } = {}) {
  let snapshot = { items: [], summary: {}, checked: "" };
  let disposed = false;
  const manage = can("checks", true);
  const summaryHost = h("div", { class: "checks-summary" });
  const tableHost = h("div");
  const extraHost = h("span", { class: "muted small" });

  const drawSummary = () => {
    const summary = snapshot.summary || {};
    const parts = [
      badge(t("checks.summary.up", { n: summary.up || 0 }), "ok"),
      badge(t("checks.summary.warn", { n: summary.warn || 0 }), summary.warn ? "warn" : "muted"),
      badge(t("checks.summary.down", { n: summary.down || 0 }), summary.down ? "bad" : "muted"),
      badge(t("checks.summary.unknown", { n: summary.unknown || 0 }), "muted"),
    ];
    if (summary.disabled) parts.push(badge(t("checks.summary.disabled", { n: summary.disabled }), "muted"));
    mount(summaryHost, h("div", { class: "chips" }, parts));
    extraHost.textContent = snapshot.checked
      ? t("checks.checkedAt", { time: fmtTime(snapshot.checked) })
      : t("checks.neverChecked");
  };

  const drawTable = () => {
    if (!snapshot.items.length) {
      mount(tableHost, empty(t("checks.empty"), {
        mark: "◎",
        hint: manage ? t("checks.emptyHint") : t("checks.emptyHintReadonly"),
        extra: manage ? button(t("checks.add"), () => editDialog(), { kind: "primary", size: "sm" }) : null,
      }));
      return;
    }
    const tableElement = table({
      columns: [
        { key: "state", label: t("checks.column.state"), width: "110px", render: (row) => stateBadge(row.state) },
        { key: "name", label: t("checks.column.name"), render: (row) => h("div", {},
          h("strong", {}, row.name),
          h("div", { class: "small muted" }, t(`checks.type.${row.type}`))) },
        { key: "target", label: t("checks.column.target"), render: (row) => h("div", { class: "check-target" },
          h("span", { class: "mono small" }, row.target),
          row.detail ? h("div", { class: "small muted check-detail", title: row.detail }, row.detail) : null) },
        { key: "latency_ms", label: t("checks.column.latency"), width: "95px", align: "right",
          render: (row) => row.latency_ms === undefined || row.latency_ms === null ? "—" : `${fmtNumber(row.latency_ms, 0)} ${t("checks.ms")}` },
        { key: "checked", label: t("checks.column.checked"), width: "130px",
          render: (row) => row.checked ? h("span", { class: "small" }, fmtTime(row.checked)) : h("span", { class: "muted small" }, t("checks.never")) },
        { key: "history", label: t("checks.column.history"), width: "210px", sortable: false, render: historyStrip },
        ...(manage ? [{ key: "actions", label: t("checks.column.actions"), width: "190px", sortable: false, render: (row) => h("div", { class: "row" },
          button(t("checks.run"), () => runOne(row), { size: "sm", kind: "ghost" }),
          button(t("common.buttons.edit"), () => editDialog(row), { size: "sm", kind: "ghost" }),
          button(t("common.buttons.delete"), () => remove(row), { size: "sm", kind: "danger" })) }] : []),
      ],
      rows: snapshot.items,
      empty: t("checks.empty"),
    });
    mount(tableHost, tableElement);
  };

  const draw = () => {
    drawSummary();
    drawTable();
  };

  const refresh = async () => {
    const result = await guard(() => api.get("/api/checks"), { quiet: true });
    if (disposed || !result || result.ok === false) return;
    snapshot = result.data || snapshot;
    draw();
  };

  const persist = async (items) => {
    const result = await guard(() => api.put("/api/checks", { checks: items }), { success: t("checks.saved") });
    if (!result) return false;
    if (result.ok === false) {
      toast(result.error || t("checks.saveFailed"), "error");
      return false;
    }
    snapshot = result.data || snapshot;
    draw();
    return true;
  };

  const runOne = async (row) => {
    const result = await guard(() => api.post("/api/checks/run", { id: row.id }), { success: t("checks.ran", { name: row.name }) });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("checks.runFailed"), "error");
      return;
    }
    snapshot = result.data || snapshot;
    draw();
  };

  const runAll = async () => {
    const result = await guard(() => api.post("/api/checks/run"), { success: t("checks.ranAll") });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("checks.runFailed"), "error");
      return;
    }
    snapshot = result.data || snapshot;
    draw();
  };

  const exportJson = () => {
    downloadReport(`megatool-checks-${reportStamp()}.json`, JSON.stringify(snapshot, null, 2), "application/json");
    toast(t("checks.exported"), "success");
  };

  const exportCsv = () => {
    const cell = (value) => {
      let text = value === null || value === undefined ? "" : String(value);
      if (/^[=+\-@]/.test(text)) text = `'${text}`;  // защита от формул в таблицах
      return `"${text.replace(/"/g, '""')}"`;
    };
    const rows = [["name", "type", "target", "state", "latency_ms", "checked", "detail", "history"]];
    for (const row of snapshot.items) {
      const history = (row.history || []).map((point) => point.ok ? (point.warn ? "W" : "U") : "D").join("");
      rows.push([row.name, row.type, row.target, row.state, row.latency_ms, row.checked, row.detail, history]);
    }
    downloadReport(`megatool-checks-${reportStamp()}.csv`, rows.map((line) => line.map(cell).join(",")).join("\r\n"),
      "text/csv;charset=utf-8");
    toast(t("checks.exported"), "success");
  };

  const remove = async (row) => {
    const ok = await confirmDialog(t("checks.deleteConfirm", { name: row.name }),
      { title: t("checks.deleteTitle"), okLabel: t("common.buttons.delete") });
    if (!ok) return;
    await persist(snapshot.items.filter((item) => item.id !== row.id).map(configOf));
  };

  const editDialog = (row = null) => {
    const source = row ? configOf(row) : {
      type: "http", interval_sec: 60, timeout_sec: 5, enabled: true,
      expected_status: 200, keyword: "", verify_tls: true, warn_days: 30,
    };
    const nameInput = input({ value: source.name || "", maxlength: "80", placeholder: t("checks.form.namePlaceholder") });
    const typeSelect = select(TYPES.map((item) => ({ value: item.value, label: t(item.key) })), { value: source.type });
    const urlInput = input({ value: source.url || "", placeholder: "https://example.com/health" });
    const hostInput = input({ value: source.host || "", placeholder: "example.com" });
    const portInput = input({ type: "number", value: String(source.port || (source.type === "tls" ? 443 : 80)), min: "1", max: "65535" });
    const expectedInput = input({ type: "number", value: String(source.expected_status || 200), min: "100", max: "599" });
    const keywordInput = input({ value: source.keyword || "", placeholder: t("checks.form.keywordPlaceholder") });
    const intervalInput = input({ type: "number", value: String(source.interval_sec || 60), min: "15", max: "3600" });
    const timeoutInput = input({ type: "number", value: String(source.timeout_sec || 5), min: "1", max: "10", step: "0.5" });
    const warnInput = input({ type: "number", value: String(source.warn_days || 30), min: "1", max: "365" });
    const verifyInput = h("input", { type: "checkbox", checked: source.verify_tls !== false });
    const enabledInput = h("input", { type: "checkbox", checked: source.enabled !== false });

    const urlField = field(t("checks.form.url"), urlInput, t("checks.form.urlHint"));
    const hostField = field(t("checks.form.host"), hostInput, t("checks.form.hostHint"));
    const portField = field(t("checks.form.port"), portInput, t("checks.form.portHint"));
    const expectedField = field(t("checks.form.expected"), expectedInput, t("checks.form.expectedHint"));
    const keywordField = field(t("checks.form.keyword"), keywordInput, t("checks.form.keywordHint"));
    const warnField = field(t("checks.form.warnDays"), warnInput, t("checks.form.warnDaysHint"));
    const verifyField = h("label", { class: "check" }, verifyInput, h("span", {}, t("checks.form.verify")));
    const verifyHint = h("div", { class: "small muted" }, t("checks.form.verifyHint"));
    const enabledField = h("label", { class: "check" }, enabledInput, h("span", {}, t("checks.form.enabled")));

    const updateVisibility = () => {
      const type = typeSelect.value;
      urlField.style.display = type === "http" ? "" : "none";
      expectedField.style.display = type === "http" ? "" : "none";
      keywordField.style.display = type === "http" ? "" : "none";
      hostField.style.display = type === "http" ? "none" : "";
      portField.style.display = type === "http" ? "none" : "";
      warnField.style.display = type === "tls" ? "" : "none";
      verifyField.style.display = type === "tcp" ? "none" : "";
      verifyHint.style.display = type === "tcp" ? "none" : "";
      if (type === "tls" && !source.port) portInput.value = "443";
      if (type === "tcp" && !source.port) portInput.value = "80";
    };
    typeSelect.addEventListener("change", updateVisibility);
    updateVisibility();

    const body = h("div", { class: "col" },
      h("div", { class: "grid cards-2" },
        field(t("checks.form.name"), nameInput, t("checks.form.nameHint")),
        field(t("checks.form.type"), typeSelect, t("checks.form.typeHint"))),
      h("div", { class: "grid cards-2" }, urlField, hostField, portField, expectedField),
      keywordField,
      h("div", { class: "grid cards-2" },
        field(t("checks.form.interval"), intervalInput, t("checks.form.intervalHint")),
        field(t("checks.form.timeout"), timeoutInput, t("checks.form.timeoutHint"))),
      h("div", { class: "col" }, warnField, verifyField, verifyHint, enabledField));

    modal({
      title: row ? t("checks.form.editTitle") : t("checks.form.addTitle"),
      width: 680,
      body,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.save"),
          kind: "primary",
          onClick: async (close) => {
            const type = typeSelect.value;
            const item = {
              ...source,
              name: nameInput.value.trim(),
              type,
              interval_sec: Number(intervalInput.value),
              timeout_sec: Number(timeoutInput.value),
              enabled: enabledInput.checked,
              verify_tls: verifyInput.checked,
            };
            if (type === "http") {
              item.url = urlInput.value.trim();
              item.expected_status = Number(expectedInput.value);
              item.keyword = keywordInput.value.trim();
            } else {
              item.host = hostInput.value.trim();
              item.port = Number(portInput.value);
              delete item.url;
              delete item.expected_status;
              delete item.keyword;
              if (type === "tls") item.warn_days = Number(warnInput.value);
              else delete item.warn_days;
            }
            const items = snapshot.items.map(configOf);
            const index = items.findIndex((entry) => entry.id && entry.id === item.id);
            if (index === -1) items.push(item);
            else items[index] = item;
            if (await persist(items)) close();
          },
        },
      ],
    });
  };

  const head = h("div", { class: "view-head" },
    h("div", {},
      h("h2", {}, t("checks.title")),
      h("p", { class: "muted" }, t("checks.subtitle"))),
    h("div", { class: "toolbar" },
      button(t("checks.exportJson"), exportJson, { size: "sm", kind: "ghost" }),
      button(t("checks.exportCsv"), exportCsv, { size: "sm", kind: "ghost" }),
      button(t("checks.refresh"), refresh, { size: "sm", kind: "ghost" }),
      manage ? button(t("checks.runAll"), runAll, { size: "sm", kind: "ghost" }) : null,
      manage ? button(t("checks.add"), () => editDialog(), { size: "sm", kind: "primary" }) : null));

  mount(root, head,
    card(t("checks.card"), h("div", { class: "col" }, summaryHost, tableHost), { extra: extraHost }));
  draw();
  const stop = poller(10000, refresh);
  return () => {
    disposed = true;
    stop();
  };
}
