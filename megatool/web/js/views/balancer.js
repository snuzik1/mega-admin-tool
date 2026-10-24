/* Балансировщик нагрузки: список, живая статистика, цели, экспорт конфигов. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import { LineChart, Sparkline } from "../charts.js";
import {
  badge, bar, button, card, chip, confirmDialog, empty, fmtNumber, fmtPct, field, guard, h,
  input, modal, mount, notice, poller, select, stateBadge, table, toast, toolbar,
} from "../ui.js";

const HEALTH_MODES = [
  { value: "tcp", key: "balancer.health.tcp" },
  { value: "http", key: "balancer.health.http" },
];

function targetRow(target = {}) {
  const row = h("div", { class: "target-row" },
    input({ placeholder: t("balancer.target.host"), value: target.host || "" , class: "input host" }),
    input({ placeholder: t("balancer.target.port"), value: target.port ?? "", class: "input port", type: "number", min: 1, max: 65535 }),
    input({ placeholder: t("balancer.target.weight"), value: target.weight ?? 1, class: "input weight", type: "number", min: 1, max: 100 }),
    input({ placeholder: t("balancer.target.note"), value: target.note || "", class: "input note" }),
    button("✕", () => row.remove(), { size: "sm", kind: "ghost", title: t("balancer.target.remove") })
  );
  row.read = () => ({
    host: row.querySelector(".host").value.trim(),
    port: Number(row.querySelector(".port").value || 0),
    weight: Number(row.querySelector(".weight").value || 1),
    note: row.querySelector(".note").value.trim(),
  });
  return row;
}

function balancerForm(balancer, algorithms, onSubmit) {
  const isEdit = Boolean(balancer);
  const data = balancer || {};
  const health = data.health || {};
  const nameInput = input({ value: data.name || "", placeholder: "web-lb", disabled: isEdit });
  const hostInput = input({ value: data.listen_host || "127.0.0.1" });
  const portInput = input({ value: data.listen_port ?? 0, type: "number", min: 0, max: 65535 });
  const noteInput = input({ value: data.note || "" });
  const algorithmSelect = select(Object.entries(algorithms || {}).map(([value, label]) => ({ value, label })), { value: data.algorithm || "round_robin" });
  const autostartInput = h("input", { type: "checkbox", checked: Boolean(data.autostart) });
  const healthEnabled = h("input", { type: "checkbox", checked: health.enabled !== false });
  const healthMode = select(HEALTH_MODES.map((mode) => ({ value: mode.value, label: t(mode.key) })), { value: health.mode || "tcp" });
  const healthPath = input({ value: health.path || "/" });
  const healthInterval = input({ value: health.interval_s ?? 10, type: "number", min: 2, max: 3600 });
  const healthTimeout = input({ value: health.timeout_s ?? 3, type: "number", min: 1, max: 30 });

  const rows = h("div", { class: "targets-editor" }, (data.targets || []).map((target) => targetRow(target)));
  const addButton = button(t("balancer.target.add"), () => rows.append(targetRow()), { kind: "ghost", size: "sm" });

  const body = h("div", {},
    h("div", { class: "split" },
      h("div", {},
        field(t("balancer.field.name"), nameInput, t("balancer.field.nameHint")),
        field(t("balancer.field.listen"), hostInput, t("balancer.field.listenHint")),
        field(t("balancer.field.port"), portInput, t("balancer.field.portHint")),
        field(t("balancer.field.note"), noteInput)),
      h("div", {},
        field(t("balancer.field.algorithm"), algorithmSelect),
        field(t("balancer.field.health"), h("div", { class: "row" }, healthEnabled, h("span", {}, t("balancer.health.on")))),
        field(t("balancer.field.healthMode"), healthMode),
        field(t("balancer.field.healthPath"), healthPath),
        field(t("balancer.field.interval"), healthInterval),
        field(t("balancer.field.timeout"), healthTimeout),
        h("label", { class: "check" }, autostartInput, t("balancer.field.autostart")))),
    h("div", { class: "field" }, h("span", { class: "field-label" }, t("balancer.field.targets")), rows, h("div", {}, addButton))
  );

  return modal({
    title: isEdit ? t("balancer.form.editTitle", { name: data.name }) : t("balancer.form.create"),
    body,
    width: 760,
    actions: [
      { label: t("common.buttons.cancel") },
      {
        label: isEdit ? t("common.buttons.save") : t("common.buttons.create"),
        kind: "primary",
        onClick: async (close) => {
          const targets = [...rows.children].map((row) => row.read()).filter((target) => target.host);
          if (!targets.length) {
            toast(t("balancer.form.needTarget"), "error");
            return;
          }
          const payload = {
            name: nameInput.value.trim(),
            listen_host: hostInput.value.trim() || "127.0.0.1",
            listen_port: Number(portInput.value || 0),
            note: noteInput.value.trim(),
            algorithm: algorithmSelect.value,
            autostart: autostartInput.checked,
            targets,
            health: {
              enabled: healthEnabled.checked,
              mode: healthMode.value,
              path: healthPath.value.trim() || "/",
              interval_s: Number(healthInterval.value || 10),
              timeout_s: Number(healthTimeout.value || 3),
            },
          };
          const result = isEdit
            ? await guard(() => api.patch(`/api/balancers/${encodeURIComponent(data.name)}`, payload), { quiet: true })
            : await guard(() => api.post("/api/balancers", payload), { quiet: true });
          if (!result || result.ok === false) {
            toast((result && result.error) || t("balancer.form.saveFailed"), "error");
            return;
          }
          toast(isEdit ? t("common.toast.dataSaved") : t("balancer.form.created"), "success");
          close();
          onSubmit();
        },
      },
    ],
  });
}

export async function render(root) {
  const cleanups = [];
  const listHost = h("div");
  let detailHost = h("div");
  let selected = null;
  let algorithms = {};
  let charts = null;
  let statsTimer = null;

  const stopDetail = () => {
    if (statsTimer) statsTimer();
    statsTimer = null;
    charts = null;
  };

  const list = async () => {
    const result = await guard(() => api.get("/api/balancers"), { quiet: true });
    if (!result) return;
    algorithms = result.algorithms || {};
    const items = result.items || [];
    mount(listHost, items.length
      ? h("div", { class: "grid cards-2" }, items.map((balancer) =>
          card(balancer.name, h("div", {},
            h("div", { class: "row row-wrap", style: { marginBottom: "8px" } },
              balancer.running ? badge(t("balancer.state.running"), "ok") : badge(t("balancer.state.stopped"), "muted"),
              badge(balancer.algorithm_label || balancer.algorithm, "neutral"),
              badge(t("balancer.badge.targets", { healthy: balancer.summary.healthy, targets: balancer.summary.targets }), balancer.summary.unhealthy ? "warn" : "ok"),
              badge(`RPS ${fmtNumber(balancer.summary.rps, 2)}`, "muted"),
              badge(t("balancer.badge.p95", { value: fmtNumber(balancer.summary.p95_ms) }), "muted"),
              h("span", { class: "grow" }),
              balancer.running
                ? button(t("common.buttons.stop"), async () => { await guard(() => api.post(`/api/balancers/${encodeURIComponent(balancer.name)}/stop`), { success: t("balancer.toast.stopped") }); await list(); }, { size: "sm", kind: "danger" })
                : button(t("common.buttons.start"), async () => { const r = await guard(() => api.post(`/api/balancers/${encodeURIComponent(balancer.name)}/start`), { quiet: true }); if (r && r.ok === false) toast(r.error || t("balancer.toast.startFailed"), "error"); else toast(t("balancer.toast.started"), "success"); await list(); }, { size: "sm", kind: "primary" }),
              button(t("common.buttons.select"), () => openDetail(balancer.name), { size: "sm", kind: "ghost" })),
            h("div", { class: "mono small muted" }, `${balancer.listen_host}:${balancer.listen_port}`),
            h("div", { class: "small muted" }, balancer.note || ""),
            balancer.error ? notice(balancer.error, "bad") : null,
            h("div", { class: "toolbar", style: { marginTop: "10px" } },
              button(t("balancer.probeNow"), async () => { const r = await guard(() => api.post(`/api/balancers/${encodeURIComponent(balancer.name)}/probe`), { quiet: true }); if (r) toast(t("balancer.toast.probeDone"), "success"); await list(); }, { size: "sm", kind: "ghost" }),
              button(t("balancer.export.button"), () => exportDialog(balancer.name), { size: "sm", kind: "ghost" }),
              button(t("common.buttons.edit"), () => balancerForm(balancer, algorithms, list), { size: "sm", kind: "ghost" }),
              button(t("common.buttons.delete"), async () => {
                if (!(await confirmDialog(t("balancer.confirmDelete", { name: balancer.name })))) return;
                await guard(() => api.del(`/api/balancers/${encodeURIComponent(balancer.name)}`), { success: t("balancer.toast.deleted") });
                if (selected === balancer.name) {
                  selected = null;
                  stopDetail();
                  mount(detailHost);
                }
                await list();
              }, { size: "sm", kind: "danger" }))), { class: "card" })))
      : empty(t("balancer.empty.title"), { mark: "⇄", hint: t("balancer.empty.hint") }));
  };

  const openDetail = (name) => {
    selected = name;
    stopDetail();
    const rpsChart = new LineChart(h("canvas", { style: { width: "100%", height: "150px" } }), {
      keys: ["rps"], title: t("balancer.chart.rps"), height: 150, format: (value) => fmtNumber(value, 2), legend: false,
    });
    const activeChart = new LineChart(h("canvas", { style: { width: "100%", height: "150px" } }), {
      keys: ["active"], title: t("balancer.chart.active"), height: 150, format: (value) => fmtNumber(value, 0), legend: false,
    });
    const p95Chart = new LineChart(h("canvas", { style: { width: "100%", height: "150px" } }), {
      keys: ["p95_ms"], title: t("balancer.chart.p95"), height: 150, format: (value) => fmtNumber(value), legend: false,
    });
    charts = { rpsChart, activeChart, p95Chart };

    const targetsHost = h("div");
    const summaryHost = h("div");

    const loadStats = async () => {
      const result = await guard(() => api.get(`/api/balancers/${encodeURIComponent(name)}/stats`), { quiet: true });
      if (!result || !result.ok) return;
      const balancer = result.data;
      const history = result.history || {};
      const payload = { ts: history.ts, series: history.series, labels: { rps: "RPS", active: t("balancer.legend.active"), p95_ms: "p95" } };
      Object.values(charts).forEach((chart) => chart.setData(payload));

      mount(summaryHost, h("div", { class: "row row-wrap" },
        balancer.running ? badge(t("balancer.state.running"), "ok") : badge(t("balancer.state.stopped"), "muted"),
        badge(t("balancer.badge.algorithm", { value: balancer.algorithm_label }), "neutral"),
        badge(t("balancer.badge.address", { value: balancer.listen }), "muted"),
        balancer.autostart ? badge(t("balancer.badge.autostart"), "neutral") : null,
        badge(t("balancer.badge.requests", { value: balancer.summary.requests }), "muted"),
        balancer.summary.errors ? badge(t("balancer.badge.errors", { value: balancer.summary.errors }), "bad") : null,
        badge(t("balancer.badge.weightTotal", { value: balancer.weight_total }), "muted")));

      mount(targetsHost, table({
        columns: [
          { key: "address", label: t("balancer.table.backend"), render: (row) => h("span", { class: "mono" }, row.address) },
          { key: "healthy", label: t("balancer.table.health"), render: (row) => row.healthy === null ? badge(t("common.noData"), "muted") : stateBadge(row.healthy ? "healthy" : "unhealthy", row.healthy ? t("balancer.health.alive") : t("balancer.health.dead")) },
          { key: "weight", label: t("balancer.table.weight"), align: "right" },
          { key: "requests", label: t("balancer.table.requests"), align: "right" },
          { key: "errors", label: t("balancer.table.errors"), align: "right", render: (row) => row.errors ? h("span", { class: "mono" }, row.errors) : "0" },
          { key: "active", label: t("balancer.table.active"), align: "right" },
          {
            key: "latency_ms", label: t("balancer.table.latency"), align: "right", width: "170px",
            render: (row) => h("div", { class: "row" },
              h("span", { class: "num mono", style: { minWidth: "52px" } }, t("balancer.table.latencyMs", { value: fmtNumber(row.latency_ms) })),
              sparkCanvas(row.latency_hist)),
          },
          { key: "last_check", label: t("balancer.table.check"), render: (row) => h("span", { class: "muted small" }, row.last_check ? row.last_check.slice(11, 19) : t("common.dash")) },
          {
            key: "note", label: t("balancer.field.note"), render: (row) => h("span", { class: "muted small" }, row.last_error ? h("span", { title: row.last_error, class: "badge badge-bad" }, t("common.status.failed")) : (row.note || t("common.dash"))),
          },
        ],
        rows: balancer.targets || [],
        empty: t("balancer.targets.empty"),
      }));
    };

    const sparkCanvas = (values) => {
      const canvas = h("canvas", { style: { width: "90px", height: "20px" } });
      const spark = new Sparkline(canvas);
      setTimeout(() => spark.setData(values), 0);
      return canvas;
    };

    mount(detailHost, card(t("balancer.detailTitle", { name }), h("div", {},
      summaryHost,
      h("div", { class: "col", style: { marginTop: "10px" } }, rpsChart.canvas, activeChart.canvas, p95Chart.canvas),
      h("div", { style: { marginTop: "10px" } }, targetsHost))));

    loadStats();
    statsTimer = poller(3000, () => {
      if (document.hidden) return;
      return loadStats();
    }, { immediate: false });
  };

  const exportDialog = (name) => {
    const content = h("pre", { class: "mono-block", style: { minHeight: "260px", maxHeight: "50vh" } }, t("common.loading"));
    const formats = ["nginx", "haproxy", "json"];
    let current = "nginx";
    const formatBar = h("div", { class: "chips" }, formats.map((format) =>
      chip(format, { active: format === current, onclick: () => { current = format; load(); } })));
    const load = async (write = false) => {
      mount(content, t("common.loading"));
      const result = await guard(() => api.get(`/api/balancers/${encodeURIComponent(name)}/export`, { format: current, write }), { quiet: true });
      if (!result) return;
      mount(content, result.content || result.error || "");
      if (write && result.path) toast(t("balancer.export.written", { path: result.path }), "success", 7000);
      [...formatBar.children].forEach((element) => element.classList.toggle("active", element.textContent === current));
    };
    modal({
      title: t("balancer.export.title", { name }),
      body: h("div", {}, formatBar, content),
      width: 860,
      actions: [
        {
          label: t("common.buttons.copy"), onClick: async () => {
            try {
              await navigator.clipboard.writeText(content.textContent || "");
              toast(t("common.copied"), "success");
            } catch (error) {
              toast(t("balancer.export.clipboardDenied"), "error");
            }
          },
        },
        { label: t("balancer.export.write"), onClick: () => load(true) },
        { label: t("common.buttons.close") },
      ],
    });
    load();
  };

  const createButton = button(t("balancer.form.create"), () => balancerForm(null, algorithms, list), { kind: "primary" });

  mount(root,
    h("div", { class: "view-head" },
      h("div", {}, h("h1", { class: "view-title" }, t("balancer.title")),
        h("div", { class: "view-sub" }, t("balancer.subtitle"))),
      h("div", { class: "toolbar" }, createButton, button(t("common.buttons.refresh"), list, { kind: "ghost" }))),
    notice(t("balancer.tip"), "warn"),
    listHost,
    detailHost);

  await list();

  cleanups.push(() => stopDetail());
  return () => cleanups.forEach((stop) => stop && stop());
}
