/* Контейнеры: Docker/Podman, статистика, логи, inspect, образы, тома,
   сети, занятое место и compose-проекты. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, bar, button, card, confirmDialog, empty, fmtBytes, fmtNumber, fmtPct,
  guard, h, jobModal, kvRows, modal, mount, notice, poller, stateBadge, table, tabs, toast, toolbar,
} from "../ui.js";

/* Действия над контейнером: в title — ключ словаря (переводится при выводе). */
const ACTIONS = [
  { id: "start", label: "▶", title: "common.buttons.start", confirm: false },
  { id: "stop", label: "■", title: "common.buttons.stop", confirm: false },
  { id: "restart", label: "↻", title: "common.buttons.restart", confirm: false },
  { id: "pause", label: "‖", title: "containers.action.pause", confirm: false },
  { id: "unpause", label: "▷", title: "containers.action.unpause", confirm: false },
  { id: "kill", label: "✕", title: "containers.action.kill", confirm: true },
  { id: "rm", label: "⌫", title: "containers.action.remove", confirm: true },
];

export async function render(root, { app } = {}) {
  const cleanups = [];
  let engineSummary = null;
  let engine = "";
  let showAll = true;
  let activeTab = "containers";

  const refreshEngines = async () => {
    const result = await guard(() => api.get("/api/engines"), { quiet: true });
    engineSummary = (result && result.data) || null;
    if (engineSummary && !engine) engine = engineSummary.active || "";
    return engineSummary;
  };

  await refreshEngines();

  const head = h("div", { class: "view-head" });
  const engineBar = h("div");
  const tabHost = h("div");
  const body = h("div");

  const drawEngineBar = () => {
    const engines = (engineSummary && engineSummary.engines) || {};
    const items = Object.entries(engines).map(([name, info]) => ({
      id: name,
      label: info.available ? `${name} ${info.server_version || info.version || ""}`.trim() : t("containers.engine.missing", { name }),
      info,
    }));
    mount(engineBar,
      h("div", { class: "row row-wrap" },
        items.map((item) =>
          h("button", {
            type: "button",
            class: `chip ${item.id === engine ? "active" : ""} ${item.info.available ? "" : "muted"}`,
            onclick: () => {
              engine = item.id;
              drawEngineBar();
              drawTab();
            },
          }, item.label)),
        h("span", { class: "grow" }),
        engineSummary && engineSummary.active
          ? badge(t("containers.engine.active", { name: engineSummary.active }), "ok")
          : badge(t("containers.engine.none"), "muted"),
        button(t("containers.engine.refresh"), async () => {
          await guard(() => api.post("/api/engines/refresh"), { success: t("containers.engine.checked") });
          await refreshEngines();
          drawEngineBar();
          drawTab();
        }, { kind: "ghost", size: "sm" })));
  };

  const engineAvailable = () => {
    const engines = (engineSummary && engineSummary.engines) || {};
    const info = engines[engine];
    return Boolean(info && info.available && info.daemon_ok);
  };

  const noEnginePlaceholder = () => {
    const engines = (engineSummary && engineSummary.engines) || {};
    const details = Object.entries(engines).map(([name, info]) =>
      h("li", {}, h("strong", {}, name), " — ", info.error || (info.available ? t("containers.engine.daemonDown") : t("containers.engine.notInPath"))));
    return empty(t("containers.noEngine.title"), {
      mark: "▣",
      hint: t("containers.noEngine.hint"),
      extra: h("div", { style: { maxWidth: "520px", margin: "14px auto 0", textAlign: "left" } },
        h("ul", { class: "list-clean" }, details),
        h("div", { class: "toolbar", style: { justifyContent: "center", marginTop: "12px" } },
          button(t("nav.settings.title"), () => (location.hash = "#/settings#engines"), { kind: "ghost" }))),
    });
  };

  /* --------------------------- контейнеры --------------------------- */
  const totalsHost = h("div");
  const listHost = h("div");

  const containerActions = (row) => {
    const buttons = ACTIONS.filter((action) => {
      if (row.state === "running") return ["stop", "restart", "pause", "kill", "rm"].includes(action.id);
      if (row.state === "paused") return ["unpause", "kill", "rm"].includes(action.id);
      return ["start", "rm"].includes(action.id);
    }).map((action) => {
      const title = t(action.title);
      return button(action.label, async () => {
        if (action.confirm && !(await confirmDialog(t("containers.confirm", { action: title, name: row.name })))) return;
        const result = await guard(() => api.post(`/api/containers/${row.id}/${action.id}`, {}, { engine: row.engine }), { quiet: true });
        if (result && result.ok === false) toast(result.error || t("containers.opFailed"), "error");
        else toast(`${title}: ${row.name}`, "success");
        setTimeout(refreshContainers, 400);
      }, { size: "xs", kind: action.confirm ? "danger" : "ghost", title });
    });
    buttons.push(button("log", () => openLogs(row), { size: "xs", kind: "ghost", title: t("containers.logs") }));
    buttons.push(button("?", () => openInspect(row), { size: "xs", kind: "ghost", title: "Inspect" }));
    return h("div", { class: "row", style: { gap: "3px" } }, buttons);
  };

  const refreshContainers = async () => {
    if (!engineAvailable()) {
      mount(totalsHost);
      mount(listHost, noEnginePlaceholder());
      return;
    }
    const result = await guard(() => api.get("/api/containers", { engine, all: showAll }), { quiet: true });
    if (!result) return;
    const data = result.data || {};
    const totals = data.totals || {};
    mount(totalsHost, h("div", { class: "row row-wrap" },
      badge(t("containers.totals.total", { n: totals.total ?? 0 }), "muted"),
      badge(t("containers.totals.running", { n: totals.running ?? 0 }), "ok"),
      badge(t("containers.totals.stopped", { n: totals.stopped ?? 0 }), "muted"),
      totals.paused ? badge(t("containers.totals.paused", { n: totals.paused }), "warn") : null,
      totals.restarting ? badge(t("containers.totals.restarting", { n: totals.restarting }), "warn") : null,
      totals.unhealthy ? badge(t("containers.totals.unhealthy", { n: totals.unhealthy }), "bad") : null,
      badge(`CPU ${fmtPct(totals.cpu_sum)}`, "neutral"),
      badge(`RAM ${totals.mem_sum_human || t("common.dash")}`, "neutral"),
      (data.errors || []).map((text) => badge(text, "bad"))));

    mount(listHost,
      (data.errors || []).length && !(data.items || []).length
        ? notice((data.errors || []).join("; "), "bad")
        : table({
            columns: [
              { key: "name", label: t("containers.col.name"), render: (row) => h("div", {}, h("div", { class: "mono" }, row.name), h("div", { class: "muted", style: { fontSize: "11px" } }, row.image)) },
              { key: "state", label: t("containers.col.state"), render: (row) => h("div", {}, stateBadge(row.state), row.health ? " " : "", row.health ? stateBadge(row.health, row.health) : "") },
              { key: "uptime", label: t("containers.col.uptime"), render: (row) => h("span", { class: "small" }, row.uptime || t("common.dash")) },
              {
                key: "cpu_pct", label: "CPU", align: "right", width: "130px",
                render: (row) => h("div", { class: "row" }, bar((row.cpu_pct || 0) / 100, { kind: row.cpu_pct > 80 ? "bad" : "" }), h("span", { class: "num mono", style: { minWidth: "44px" } }, fmtPct(row.cpu_pct))),
              },
              {
                key: "mem_used", label: t("containers.col.memory"), align: "right", width: "150px",
                render: (row) => h("div", {}, h("div", { class: "mono small" }, `${row.mem_used_human || t("common.dash")}${row.mem_limit ? ` / ${fmtBytes(row.mem_limit)}` : ""}`), bar(row.mem_limit ? (row.mem_used || 0) / row.mem_limit : (row.mem_pct || 0) / 100)),
              },
              { key: "ports", label: t("containers.col.ports"), render: (row) => h("span", { class: "mono small" }, (row.ports || []).join(", ") || t("common.dash")) },
              { key: "project", label: t("containers.col.project"), render: (row) => h("span", { class: "muted small" }, row.project ? `${row.project}/${row.service}` : t("common.dash")) },
              { key: "actions", label: "", render: containerActions },
            ],
            rows: data.items || [],
            empty: showAll ? t("containers.empty.containers") : t("containers.empty.running"),
          }));
  };

  const openLogs = async (row) => {
    const result = await guard(() => api.get(`/api/containers/${row.id}/logs`, { engine: row.engine, tail: 300 }), { quiet: true });
    const text = result && result.ok ? result.text || "" : (result && result.error) || t("containers.logsFailed");
    modal({
      title: t("containers.logsTitle", { name: row.name }),
      body: h("pre", { class: "mono-block", style: { maxHeight: "60vh", minHeight: "300px" } }, text || t("containers.empty.list")),
      width: 900,
      actions: [{ label: t("common.buttons.refresh"), onClick: () => { }, disabled: true }, { label: t("common.buttons.close") }],
    });
  };

  const openInspect = async (row) => {
    const result = await guard(() => api.get(`/api/containers/${row.id}`, { engine: row.engine }), { quiet: true });
    const data = result && result.data ? result.data : [];
    const first = Array.isArray(data) ? data[0] || {} : data;
    const config = first.Config || {};
    const state = first.State || {};
    const network = (first.NetworkSettings && first.NetworkSettings.Networks) || {};
    modal({
      title: `Inspect: ${row.name}`,
      body: h("div", {},
        kvRows([
          ["ID", String(first.Id || row.id).slice(0, 24)],
          [t("containers.kv.image"), config.Image],
          [t("containers.kv.command"), (config.Cmd || []).join(" ")],
          ["Entrypoint", (config.Entrypoint || []).join(" ")],
          [t("containers.kv.state"), state.Status],
          [t("containers.kv.exitCode"), state.ExitCode],
          [t("containers.kv.started"), state.StartedAt],
          [t("containers.kv.finished"), state.FinishedAt],
          ["Restart count", first.RestartCount],
          [t("containers.kv.platform"), first.Platform],
          [t("containers.kv.networks"), Object.keys(network).join(", ") || t("common.dash")],
        ]),
        h("details", { class: "raw", style: { marginTop: "10px" } },
          h("summary", {}, t("containers.fullJson")),
          h("pre", { class: "mono-block", style: { maxHeight: "45vh" } }, JSON.stringify(first, null, 2)))
      ),
      width: 860,
      actions: [{ label: t("common.buttons.close") }],
    });
  };

  /* --------------------------- ресурсы ------------------------------ */
  const resourceHost = h("div");

  const loadResource = async (kind) => {
    if (!engineAvailable()) {
      mount(resourceHost, noEnginePlaceholder());
      return;
    }
    const result = await guard(() => api.get(`/api/engine/${kind}`, { engine, limit: 100 }), { quiet: true });
    if (!result) return;
    const items = result.items || [];
    const definitions = {
      images: [
        { key: "repository", label: t("containers.col.repository") },
        { key: "tag", label: t("containers.col.tag") },
        { key: "size", label: t("containers.col.size"), align: "right" },
        { key: "created", label: t("containers.col.created") },
        { key: "id", label: "ID", render: (row) => h("span", { class: "mono muted" }, row.id) },
      ],
      volumes: [
        { key: "name", label: t("containers.col.name"), render: (row) => h("span", { class: "mono" }, row.name) },
        { key: "driver", label: t("containers.col.driver") },
        { key: "mountpoint", label: t("containers.col.mountpoint"), render: (row) => h("span", { class: "mono muted small" }, row.mountpoint) },
      ],
      networks: [
        { key: "name", label: t("containers.col.name"), render: (row) => h("span", { class: "mono" }, row.name) },
        { key: "driver", label: t("containers.col.driver") },
        { key: "scope", label: t("containers.col.scope") },
        { key: "id", label: "ID", render: (row) => h("span", { class: "mono muted" }, row.id) },
      ],
    };
    mount(resourceHost, table({ columns: definitions[kind], rows: items, empty: t("containers.empty.list") }));
  };

  const dfHost = h("div");
  const loadDf = async () => {
    if (!engineAvailable()) {
      mount(dfHost, noEnginePlaceholder());
      return;
    }
    const result = await guard(() => api.get("/api/engine/df", { engine }), { quiet: true });
    if (!result) return;
    mount(dfHost, table({
      columns: [
        { key: "type", label: t("containers.col.type") },
        { key: "total", label: t("containers.col.total"), align: "right" },
        { key: "active", label: t("containers.col.active"), align: "right" },
        { key: "size", label: t("containers.col.size") },
        { key: "reclaimable", label: t("containers.col.reclaimable") },
      ],
      rows: result.items || [],
      empty: result.error || t("common.noData"),
    }));
  };

  /* --------------------------- compose ------------------------------ */
  const composeHost = h("div");

  const composeAction = async (project, action) => {
    const result = await guard(() => api.post(`/api/compose/${action}`, { dir: project.dir, engine: project.engine || engine }), { quiet: true });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("containers.compose.actionFailed"), "error");
      return;
    }
    const job = (result.data && result.data.job) || result.job;
    if (job) jobModal(job, { title: `compose ${action}: ${project.name}` });
    setTimeout(loadCompose, 2500);
  };

  const loadCompose = async () => {
    if (!engineAvailable()) {
      mount(composeHost, noEnginePlaceholder());
      return;
    }
    const result = await guard(() => api.get("/api/compose/projects", { engine }), { quiet: true });
    if (!result) return;
    const projects = result.items || [];
    mount(composeHost, projects.length
      ? h("div", { class: "col" }, projects.map((project) =>
          card(project.name, h("div", {},
            h("div", { class: "row row-wrap" },
              badge(t("containers.compose.running", { running: project.running ?? 0, total: project.total ?? 0 }), project.total && project.running === project.total ? "ok" : project.running ? "warn" : "muted"),
              h("span", { class: "mono muted small" }, project.dir),
              h("span", { class: "grow" }),
              ["up", "down", "restart", "stop", "pull", "build", "logs"].map((action) =>
                button(action, () => composeAction(project, action), { size: "sm", kind: ["down", "stop"].includes(action) ? "danger" : "ghost" }))),
            project.state_error ? notice(project.state_error, "bad") : null,
            (project.state || []).length
              ? table({
                  columns: [
                    { key: "service", label: t("containers.col.service") },
                    { key: "name", label: t("containers.col.container"), render: (row) => h("span", { class: "mono small" }, row.name) },
                    { key: "state", label: t("containers.col.state"), render: (row) => stateBadge(row.state) },
                    { key: "health", label: "Health", render: (row) => row.health ? stateBadge(row.health, row.health) : t("common.dash") },
                    { key: "ports", label: t("containers.col.ports"), render: (row) => h("span", { class: "mono small" }, (row.ports || []).join(", ") || t("common.dash")) },
                  ],
                  rows: project.state,
                })
              : null), { class: "card" })))
      : empty(t("containers.compose.empty"), { mark: "▤", hint: t("containers.compose.emptyHint") }));
  };

  /* --------------------------- вкладки ------------------------------ */
  const drawTab = () => {
    switch (activeTab) {
      case "containers":
        mount(tabHost, toolbar(
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: showAll, onchange: (event) => { showAll = event.target.checked; refreshContainers(); } }), t("containers.showStopped")),
          h("span", { class: "spacer" }),
          button(t("common.buttons.refresh"), refreshContainers, { kind: "ghost", size: "sm" })),
          totalsHost, listHost);
        refreshContainers();
        break;
      case "images": mount(tabHost, resourceHost); loadResource("images"); break;
      case "volumes": mount(tabHost, resourceHost); loadResource("volumes"); break;
      case "networks": mount(tabHost, resourceHost); loadResource("networks"); break;
      case "df": mount(tabHost, dfHost); loadDf(); break;
      case "compose": mount(tabHost, composeHost); loadCompose(); break;
    }
  };

  const tabBar = tabs(
    [
      { id: "containers", label: t("containers.tabs.containers") },
      { id: "compose", label: t("containers.tabs.compose") },
      { id: "images", label: t("containers.tabs.images") },
      { id: "volumes", label: t("containers.tabs.volumes") },
      { id: "networks", label: t("containers.tabs.networks") },
      { id: "df", label: t("containers.tabs.df") },
    ],
    { onChange: (id) => { activeTab = id; drawTab(); } }
  );

  mount(root,
    head,
    h("div", { class: "col" }, engineBar, tabBar, body));
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, t("containers.title")),
      h("div", { class: "view-sub" }, t("containers.subtitle"))),
    h("div", { class: "toolbar" },
      button(t("containers.openCatalog"), () => (location.hash = "#/catalog"), { kind: "ghost" })));

  // вкладки живут в body, чтобы не пересоздавать при смене содержимого
  mount(body, tabHost);
  drawEngineBar();
  drawTab();

  cleanups.push(poller(9000, async () => {
    if (document.hidden || activeTab !== "containers") return;
    await refreshContainers();
  }, { immediate: false }));

  return () => cleanups.forEach((stop) => stop && stop());
}
