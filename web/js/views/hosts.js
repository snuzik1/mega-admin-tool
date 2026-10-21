/* Серверы: инвентарь, доступность, uptime, простой и выключенное время.
   Проверки выполняет сервер в фоне, здесь — управление и таблица. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, can, card, confirmDialog, empty, field, fmtDuration, fmtTime, guard, h, input,
  modal, mount, poller, select, table, timeAgo, toast,
} from "../ui.js";

const PROBES = [
  { value: "tcp", key: "hosts.form.probeTcp" },
  { value: "http", key: "hosts.form.probeHttp" },
];

const STATE_KIND = { up: "ok", down: "bad", unknown: "muted", disabled: "muted" };

const configOf = (row) => {
  const item = {
    id: row.id,
    name: row.name,
    kind: row.kind || "",
    note: row.note || "",
    probe: row.probe,
    interval_sec: Number(row.interval_sec) || 60,
    timeout_sec: Number(row.timeout_sec) || 5,
    enabled: row.enabled !== false,
  };
  if (row.probe === "http") {
    item.url = row.url;
    item.expected_status = Number(row.expected_status) || 200;
    item.keyword = row.keyword || "";
    item.verify_tls = row.verify_tls !== false;
  } else {
    item.host = row.host;
    item.port = Number(row.port) || 443;
  }
  return item;
};

function stateBadge(row) {
  return badge(
    t(`hosts.state.${row.state || "unknown"}`),
    STATE_KIND[row.state] || "muted",
    t("hosts.checksCount", { ok: row.ok_count || 0, fail: row.fail_count || 0 })
  );
}

function durationCell(row, key, totalKey) {
  const value = Number(row[key]) || 0;
  const total = Number(row[totalKey]) || 0;
  const title = `${t("hosts.durationHint")} ${fmtDuration(total)}`;
  const current = h("span", { class: value ? "num mono" : "muted", title },
    value ? fmtDuration(value) : t("common.dash"));
  if (total <= value + 0.5) return current;
  // Суммарное время остаётся видимым и после выключения/восстановления,
  // когда текущая длительность состояния обнуляется.
  return h("div", {},
    current,
    h("div", { class: "small muted" }, t("hosts.totalLabel", { duration: fmtDuration(total) })));
}

export async function render(root) {
  let snapshot = { items: [], summary: {}, checked: "" };
  let disposed = false;
  const manage = can("hosts", true);
  const summaryHost = h("div", { class: "checks-summary" });
  const tableHost = h("div");
  const extraHost = h("span", { class: "muted small" });

  const drawSummary = () => {
    const summary = snapshot.summary || {};
    mount(summaryHost, h("div", { class: "chips" },
      badge(t("hosts.summary.total", { n: summary.total || 0 }), "muted"),
      badge(t("hosts.summary.up", { n: summary.up || 0 }), "ok"),
      badge(t("hosts.summary.down", { n: summary.down || 0 }), summary.down ? "bad" : "muted"),
      badge(t("hosts.summary.unknown", { n: summary.unknown || 0 }), "muted"),
      badge(t("hosts.summary.disabled", { n: summary.disabled || 0 }), "muted")));
    extraHost.textContent = snapshot.checked
      ? t("hosts.checkedAt", { time: fmtTime(snapshot.checked) })
      : t("hosts.neverChecked");
  };

  const drawTable = () => {
    if (!snapshot.items.length) {
      mount(tableHost, empty(t("hosts.empty"), {
        mark: "▥",
        hint: manage ? t("hosts.emptyHint") : t("hosts.emptyHintReadonly"),
        extra: manage ? button(t("hosts.add"), () => editDialog(), { kind: "primary", size: "sm" }) : null,
      }));
      return;
    }
    mount(tableHost, table({
      columns: [
        { key: "state", label: t("hosts.column.state"), width: "120px", render: stateBadge },
        {
          key: "name", label: t("hosts.column.name"), render: (row) => h("div", {},
            h("div", { class: "row row-wrap" },
              h("strong", {}, row.name),
              badge(row.kind || t("hosts.kindDefault"), "neutral"),
              badge(row.enabled === false ? t("hosts.monitoringOff") : t("hosts.monitoringOn"),
                row.enabled === false ? "muted" : "ok")),
            row.note ? h("div", { class: "small muted" }, row.note) : null),
        },
        {
          key: "target", label: t("hosts.column.target"), render: (row) => h("div", { class: "check-target" },
            h("span", { class: "mono small" }, row.target),
            h("div", { class: "small muted" }, t(`hosts.form.probe${row.probe === "http" ? "Http" : "Tcp"}`)),
            row.detail ? h("div", { class: "small muted check-detail", title: row.detail }, row.detail) : null),
        },
        { key: "uptime_sec", label: t("hosts.column.uptime"), width: "120px", align: "right",
          render: (row) => durationCell(row, "uptime_sec", "total_uptime_sec") },
        { key: "downtime_sec", label: t("hosts.column.downtime"), width: "120px", align: "right",
          render: (row) => durationCell(row, "downtime_sec", "total_downtime_sec") },
        { key: "disabled_sec", label: t("hosts.column.disabled"), width: "120px", align: "right",
          render: (row) => durationCell(row, "disabled_sec", "total_disabled_sec") },
        { key: "last_check", label: t("hosts.column.checked"), width: "130px",
          render: (row) => row.last_check
            ? h("span", { class: "small", title: fmtTime(row.last_check) }, timeAgo(row.last_check))
            : h("span", { class: "muted small" }, t("common.dash")) },
        ...(manage ? [{
          key: "actions", label: t("hosts.column.actions"), width: "280px", sortable: false,
          render: (row) => h("div", { class: "row row-wrap" },
            button(t("hosts.run"), () => runOne(row), {
              size: "sm", kind: "ghost", disabled: row.state === "disabled",
            }),
            button(row.enabled === false ? t("hosts.enable") : t("hosts.disable"), () => toggle(row), {
              size: "sm", kind: "ghost",
            }),
            button(t("common.buttons.edit"), () => editDialog(row), { size: "sm", kind: "ghost" }),
            button(t("common.buttons.delete"), () => remove(row), { size: "sm", kind: "danger" })),
        }] : []),
      ],
      rows: snapshot.items,
      empty: t("hosts.empty"),
    }));
  };

  const draw = () => {
    drawSummary();
    drawTable();
  };

  const refresh = async () => {
    const result = await guard(() => api.get("/api/hosts"), { quiet: true });
    if (disposed || !result || result.ok === false) return;
    snapshot = result.data || snapshot;
    draw();
  };

  const persist = async (items, success = t("hosts.saved")) => {
    const result = await guard(() => api.put("/api/hosts", { hosts: items }), { success });
    if (!result) return false;
    if (result.ok === false) {
      toast(result.error || t("hosts.saveFailed"), "error");
      return false;
    }
    snapshot = result.data || snapshot;
    draw();
    return true;
  };

  const runOne = async (row) => {
    if (row.state === "disabled") return;
    const result = await guard(() => api.post("/api/hosts/run", { id: row.id }), {
      success: t("hosts.ran", { name: row.name }),
    });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("hosts.runFailed"), "error");
      return;
    }
    snapshot = result.data || snapshot;
    draw();
  };

  const runAll = async () => {
    const result = await guard(() => api.post("/api/hosts/run"), { success: t("hosts.ranAll") });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("hosts.runFailed"), "error");
      return;
    }
    snapshot = result.data || snapshot;
    draw();
  };

  const toggle = async (row) => {
    const items = snapshot.items.map(configOf);
    const target = items.find((item) => item.id === row.id);
    if (!target) return;
    target.enabled = target.enabled === false;
    await persist(items, target.enabled ? t("hosts.enabledToast") : t("hosts.disabledToast"));
  };

  const remove = async (row) => {
    const ok = await confirmDialog(t("hosts.deleteConfirm", { name: row.name }), {
      title: t("hosts.deleteTitle"), okLabel: t("common.buttons.delete"),
    });
    if (!ok) return;
    await persist(snapshot.items.filter((item) => item.id !== row.id).map(configOf));
  };

  const editDialog = (row = null) => {
    const source = row ? configOf(row) : {
      probe: "tcp", interval_sec: 60, timeout_sec: 5, enabled: true,
      expected_status: 200, keyword: "", verify_tls: true, port: 443,
    };
    const nameInput = input({ value: source.name || "", maxlength: "80", placeholder: t("hosts.form.namePlaceholder") });
    const kindInput = input({ value: source.kind || "", maxlength: "40", placeholder: t("hosts.form.kindPlaceholder") });
    const noteInput = h("textarea", {
      class: "input", rows: "3", maxlength: "300",
      placeholder: t("hosts.form.notePlaceholder"), value: source.note || "",
    });
    const probeSelect = select(PROBES.map((item) => ({ value: item.value, label: t(item.key) })), { value: source.probe });
    const hostInput = input({ value: source.host || "", placeholder: "example.com" });
    const portInput = input({ type: "number", value: String(source.port || 443), min: "1", max: "65535" });
    const urlInput = input({ value: source.url || "", placeholder: "https://example.com/health" });
    const expectedInput = input({ type: "number", value: String(source.expected_status || 200), min: "100", max: "599" });
    const keywordInput = input({ value: source.keyword || "", placeholder: t("hosts.form.keywordPlaceholder") });
    const intervalInput = input({ type: "number", value: String(source.interval_sec || 60), min: "15", max: "3600" });
    const timeoutInput = input({ type: "number", value: String(source.timeout_sec || 5), min: "1", max: "10", step: "0.5" });
    const verifyInput = h("input", { type: "checkbox", checked: source.verify_tls !== false });
    const enabledInput = h("input", { type: "checkbox", checked: source.enabled !== false });

    const urlField = field(t("hosts.form.url"), urlInput, t("hosts.form.urlHint"));
    const hostField = field(t("hosts.form.host"), hostInput, t("hosts.form.hostHint"));
    const portField = field(t("hosts.form.port"), portInput, t("hosts.form.portHint"));
    const expectedField = field(t("hosts.form.expected"), expectedInput, t("hosts.form.expectedHint"));
    const keywordField = field(t("hosts.form.keyword"), keywordInput, t("hosts.form.keywordHint"));
    const verifyField = h("label", { class: "check" }, verifyInput, h("span", {}, t("hosts.form.verify")));
    const verifyHint = h("div", { class: "small muted" }, t("hosts.form.verifyHint"));
    const enabledField = h("label", { class: "check" }, enabledInput, h("span", {}, t("hosts.form.enabled")));

    const updateVisibility = () => {
      const probe = probeSelect.value;
      const http = probe === "http";
      urlField.style.display = http ? "" : "none";
      expectedField.style.display = http ? "" : "none";
      keywordField.style.display = http ? "" : "none";
      verifyField.style.display = http ? "" : "none";
      verifyHint.style.display = http ? "" : "none";
      hostField.style.display = http ? "none" : "";
      portField.style.display = http ? "none" : "";
    };
    probeSelect.addEventListener("change", updateVisibility);
    updateVisibility();

    const body = h("div", { class: "col" },
      h("div", { class: "grid cards-2" },
        field(t("hosts.form.name"), nameInput, t("hosts.form.nameHint")),
        field(t("hosts.form.kind"), kindInput, t("hosts.form.kindHint"))),
      field(t("hosts.form.note"), noteInput, t("hosts.form.noteHint")),
      h("div", { class: "grid cards-2" },
        field(t("hosts.form.probe"), probeSelect, t("hosts.form.probeHint")),
        enabledField),
      h("div", { class: "grid cards-2" }, urlField, hostField, portField, expectedField),
      keywordField,
      h("div", { class: "grid cards-2" },
        field(t("hosts.form.interval"), intervalInput, t("hosts.form.intervalHint")),
        field(t("hosts.form.timeout"), timeoutInput, t("hosts.form.timeoutHint"))),
      h("div", { class: "col" }, verifyField, verifyHint));

    modal({
      title: row ? t("hosts.form.editTitle") : t("hosts.form.addTitle"),
      width: 680,
      body,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.save"),
          kind: "primary",
          onClick: async (close) => {
            const item = {
              ...source,
              name: nameInput.value.trim(),
              kind: kindInput.value.trim(),
              note: noteInput.value.trim(),
              probe: probeSelect.value,
              interval_sec: Number(intervalInput.value),
              timeout_sec: Number(timeoutInput.value),
              enabled: enabledInput.checked,
            };
            if (item.probe === "http") {
              item.url = urlInput.value.trim();
              item.expected_status = Number(expectedInput.value);
              item.keyword = keywordInput.value.trim();
              item.verify_tls = verifyInput.checked;
              delete item.host;
              delete item.port;
            } else {
              item.host = hostInput.value.trim();
              item.port = Number(portInput.value);
              delete item.url;
              delete item.expected_status;
              delete item.keyword;
              delete item.verify_tls;
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
      h("h2", {}, t("hosts.title")),
      h("p", { class: "muted" }, t("hosts.subtitle"))),
    h("div", { class: "toolbar" },
      button(t("hosts.refresh"), refresh, { size: "sm", kind: "ghost" }),
      manage ? button(t("hosts.runAll"), runAll, { size: "sm", kind: "ghost" }) : null,
      manage ? button(t("hosts.add"), () => editDialog(), { size: "sm", kind: "primary" }) : null));

  mount(root, head,
    card(t("hosts.card"), h("div", { class: "col" }, summaryHost, tableHost), { extra: extraHost }));
  draw();
  const stop = poller(10000, refresh);
  return () => {
    disposed = true;
    stop();
  };
}
