/* Обзор: живые графики, индикаторы, процессы, диски, сеть, предупреждения. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import { Bars, Gauge, LineChart } from "../charts.js";
import {
  badge, bar, bus, button, card, confirmDialog, empty, fmtBytes, fmtDuration, fmtNumber, fmtPct,
  guard, h, input, jobModal, kvRows, modal, mount, notice, poller, stateBadge, table, toast, toolbar,
} from "../ui.js";

const SERIES = ["cpu", "mem_pct", "swap_pct", "load1", "net_rx", "net_tx", "disk_read", "disk_write", "disk_pct"];
const RANGES = [
  { id: 5, key: "dashboard.range.min5" },
  { id: 30, key: "dashboard.range.min30" },
  { id: 120, key: "dashboard.range.h2" },
  { id: 720, key: "dashboard.range.h12" },
];

export async function render(root, { args = [], desc = "" } = {}) {
  const cleanups = [];
  const buffer = { ts: [], series: {}, loaded: false, range: 30 };

  let snapshot = null;
  try {
    snapshot = await api.get("/api/metrics/snapshot", { details: true });
  } catch (error) {
    mount(root, notice(t("dashboard.metricsFailed", { error: error.message }), "bad"));
    return null;
  }
  const info = (snapshot.data && snapshot.data.info) || {};
  let point = (snapshot.data && snapshot.data.point) || {};
  const initialAlerts = (snapshot.data && snapshot.data.alerts) || [];

  /* --------------------------- индикаторы --------------------------- */
  const gaugeCpu = new Gauge(h("canvas"), { label: "CPU", max: 100 });
  const gaugeMem = new Gauge(h("canvas"), { label: t("dashboard.gauge.mem"), max: 100 });
  const gaugeDisk = new Gauge(h("canvas"), { label: t("dashboard.gauge.disk"), max: 100 });
  const gaugeSwap = new Gauge(h("canvas"), { label: "Swap", max: 100 });
  const coresCanvas = h("canvas", { style: { width: "100%", height: "60px" } });
  const cores = new Bars(coresCanvas, { max: 100 });

  const statLoad = h("div", { class: "stat" });
  const statProc = h("div", { class: "stat" });
  const statNet = h("div", { class: "stat" });
  const statTemp = h("div", { class: "stat" });
  const statUptime = h("div", { class: "stat" });
  const statMem = h("div", { class: "stat" });

  const gaugeCard = card(t("dashboard.card.load"), h("div", {},
    h("div", { class: "gauges" },
      h("div", { class: "gauge-cell" }, gaugeCpu.canvas),
      h("div", { class: "gauge-cell" }, gaugeMem.canvas),
      h("div", { class: "gauge-cell" }, gaugeDisk.canvas),
      h("div", { class: "gauge-cell" }, gaugeSwap.canvas)),
    h("div", { class: "grid cards-4", style: { marginTop: "12px" } },
      statLoad, statProc, statNet, statTemp, statMem, statUptime),
    h("div", { style: { marginTop: "10px" } }, h("div", { class: "stat-label" }, t("dashboard.coresLoad")), coresCanvas)
  ));

  /* --------------------------- графики ------------------------------ */
  const charts = {
    compute: new LineChart(h("canvas", { style: { width: "100%", height: "180px" } }), {
      keys: ["cpu", "mem_pct", "swap_pct"], title: t("dashboard.chart.compute"), height: 180,
      format: (value) => `${Number(value.toFixed(1))}`,
    }),
    load: new LineChart(h("canvas", { style: { width: "100%", height: "160px" } }), {
      keys: ["load1"], title: t("dashboard.chart.load"), height: 160,
      format: (value) => Number(value.toFixed(2)).toString(),
    }),
    net: new LineChart(h("canvas", { style: { width: "100%", height: "160px" } }), {
      keys: ["net_rx", "net_tx"], title: t("dashboard.chart.net", { unit: t("common.units")[0] }), height: 160,
      format: (value) => t("common.perSec", { value: fmtBytes(value) }),
    }),
    disk: new LineChart(h("canvas", { style: { width: "100%", height: "160px" } }), {
      keys: ["disk_read", "disk_write"], title: t("dashboard.chart.disk", { unit: t("common.units")[0] }), height: 160,
      format: (value) => t("common.perSec", { value: fmtBytes(value) }),
    }),
    diskPct: new LineChart(h("canvas", { style: { width: "100%", height: "150px" } }), {
      keys: ["disk_pct"], title: t("dashboard.chart.diskPct"), height: 150, zero: true,
      format: (value) => fmtPct(value),
    }),
  };

  let paused = false;
  const rangeChips = h("div", { class: "chips" }, RANGES.map((range) =>
    h("button", {
      type: "button",
      class: `chip ${range.id === 30 ? "active" : ""}`,
      onclick: () => {
        buffer.range = range.id;
        rangeChips.querySelectorAll(".chip").forEach((element) => element.classList.toggle("active", element.textContent === t(range.key)));
        for (const chart of Object.values(charts)) chart.options.maxPoints = Math.max(120, (range.id * 60) / 2);
        loadHistory();
      },
    }, t(range.key))));
  const pauseButton = button(t("dashboard.pause"), () => {
    paused = !paused;
    pauseButton.textContent = paused ? t("dashboard.resume") : t("dashboard.pause");
  });

  const chartsCard = card(t("dashboard.card.dynamics"), h("div", { class: "col" },
    charts.compute.canvas, charts.load.canvas,
    h("div", { class: "split" }, h("div", {}, charts.net.canvas), h("div", {}, charts.disk.canvas)),
    charts.diskPct.canvas
  ), { extra: toolbar(rangeChips, pauseButton) });

  /* --------------------------- предупреждения ----------------------- */
  const alertsHost = h("div");
  const alertsCard = card(t("dashboard.card.alerts"), alertsHost, {
    id: "alerts",
    extra: h("button", {
      type: "button",
      class: "link-btn",
      onclick: () => (location.hash = "#/settings/monitor"),
    }, t("dashboard.alertsHint")),
  });
  const renderAlerts = (items) => {
    mount(alertsHost, items.length
      ? h("div", {}, items.map((alert) =>
          h("div", { class: "alert-item" },
            h("span", { class: "severity" }, stateBadge(alert.severity, alert.severity === "critical" ? t("dashboard.sev.critical") : alert.severity === "warning" ? t("dashboard.sev.warning") : t("dashboard.sev.info"))),
            h("div", { class: "alert-body" },
              h("div", { class: "alert-title" }, alert.title),
              h("div", { class: "alert-detail" }, alert.detail || ""),
              h("div", { class: "muted small" }, [alert.source, alert.since ? t("dashboard.since", { since: alert.since }) : ""].filter(Boolean).join(" • "))),
            button(t("dashboard.hideAlert"), async () => {
              await guard(() => api.post(`/api/alerts/${encodeURIComponent(alert.id)}/dismiss`));
              renderAlerts(items.filter((item) => item !== alert));
            }, { size: "sm", kind: "ghost" }))))
      : empty(t("dashboard.noAlerts"), { mark: "✓" }));
  };
  renderAlerts(initialAlerts);

  /* --------------------------- паспорт машины ----------------------- */
  const host = info.host || {};
  const cpu = info.cpu || {};
  const infoCard = card(t("dashboard.card.machine"), kvRows([
    [t("dashboard.kv.hostname"), host.hostname],
    [t("dashboard.kv.os"), host.os_pretty],
    [t("dashboard.kv.kernel"), host.kernel],
    [t("dashboard.kv.arch"), host.arch],
    [t("dashboard.kv.virt"), host.virtualization || t("common.dash")],
    [t("dashboard.kv.cpu"), cpu.model],
    [t("dashboard.kv.cores"), t("dashboard.cores", { phys: cpu.cores_physical ?? "?", log: cpu.cores_logical ?? "?" })],
    [t("dashboard.kv.freq"), cpu.freq_mhz ? t("common.megahertz", { value: fmtNumber(cpu.freq_mhz, 0) }) : t("common.dash")],
    [t("dashboard.kv.memTotal"), fmtBytes((info.memory || {}).total)],
    [t("dashboard.kv.uptime"), host.uptime_human || fmtDuration(host.uptime)],
    [t("dashboard.kv.privileges"), host.is_admin ? t("dashboard.admin") : t("dashboard.user")],
    ["Python", (info.app || {}).python],
  ]), { extra: badge(`uptime ${host.uptime_human || t("common.dash")}`) });

  /* --------------------------- удалённые агенты ---------------------- */
  const agentsHost = h("div");
  const agentsCard = card(t("dashboard.card.agents"), agentsHost, {
    extra: h("span", { class: "muted small" }, t("dashboard.agentsHint")),
  });

  const refreshAgents = async () => {
    const result = await guard(() => api.get("/api/agents"), { quiet: true });
    if (!result) return;
    const agents = (result.data || {}).agents || [];
    if (!agents.length) {
      mount(agentsHost, empty(t("dashboard.agents.empty"), {
        mark: "◉",
        hint: t("dashboard.agents.emptyHint"),
        extra: button(t("nav.settings.title"), () => (location.hash = "#/settings/agents"), { size: "sm", kind: "ghost" }),
      }));
      return;
    }
    mount(agentsHost, h("div", {}, agents.map((agent) => {
      const metrics = agent.metrics || {};
      return h("div", { class: "alert-item" },
        h("span", { class: "severity" },
          badge(agent.online ? t("dashboard.agents.online") : t("dashboard.agents.offline"),
            agent.online ? "ok" : "bad", agent.error || "")),
        h("div", { class: "alert-body" },
          h("div", { class: "alert-title" }, agent.name),
          h("div", { class: "mono small muted" }, agent.url),
          agent.online
            ? h("div", { class: "alert-detail" }, t("dashboard.agents.load", {
                cpu: fmtNumber(metrics.cpu_pct),
                mem: fmtNumber(metrics.mem_pct),
                disk: fmtNumber(metrics.disk_pct),
              }))
            : h("div", { class: "alert-detail" }, agent.error || t("dashboard.agents.error"))));
    })));
  };

  /* --------------------------- серверы ------------------------------ */
  const hostsHost = h("div");
  const hostsCard = card(t("dashboard.card.hosts"), hostsHost, {
    extra: h("span", { class: "muted small" }, t("dashboard.hostsHint")),
  });

  const refreshHosts = async () => {
    const result = await guard(() => api.get("/api/hosts"), { quiet: true });
    if (!result || result.ok === false) return;
    const data = result.data || {};
    const items = data.items || [];
    const summary = data.summary || {};
    if (!items.length) {
      mount(hostsHost, empty(t("dashboard.hosts.empty"), {
        mark: "▥",
        hint: t("dashboard.hosts.emptyHint"),
        extra: button(t("dashboard.hosts.add"), () => (location.hash = "#/hosts"), { size: "sm", kind: "primary" }),
      }));
      return;
    }
    const order = { down: 0, unknown: 1, up: 2, disabled: 3 };
    const shown = [...items].sort((left, right) => (order[left.state] ?? 9) - (order[right.state] ?? 9)).slice(0, 8);
    const stateKind = { up: "ok", down: "bad", unknown: "muted", disabled: "muted" };
    const duration = (value) => Number(value) > 0 ? fmtDuration(value) : t("common.dash");
    mount(hostsHost, h("div", { class: "col" },
      h("div", { class: "chips" },
        badge(t("dashboard.hosts.up", { n: summary.up || 0 }), "ok"),
        badge(t("dashboard.hosts.down", { n: summary.down || 0 }), summary.down ? "bad" : "muted"),
        badge(t("dashboard.hosts.unknown", { n: summary.unknown || 0 }), "muted"),
        badge(t("dashboard.hosts.disabled", { n: summary.disabled || 0 }), "muted")),
      table({
        columns: [
          {
            key: "state", label: t("hosts.column.state"), width: "105px",
            render: (row) => badge(t(`hosts.state.${row.state || "unknown"}`), stateKind[row.state] || "muted"),
          },
          {
            key: "name", label: t("hosts.column.name"),
            render: (row) => h("div", {},
              h("div", { class: "row row-wrap" }, h("strong", {}, row.name),
                badge(row.kind || t("hosts.kindDefault"), "neutral")),
              row.note ? h("div", { class: "small muted" }, row.note) : null),
          },
          { key: "uptime_sec", label: t("hosts.column.uptime"), width: "110px", align: "right",
            render: (row) => duration(row.uptime_sec) },
          { key: "downtime_sec", label: t("hosts.column.downtime"), width: "110px", align: "right",
            render: (row) => duration(row.downtime_sec) },
          { key: "disabled_sec", label: t("hosts.column.disabled"), width: "110px", align: "right",
            render: (row) => duration(row.disabled_sec) },
        ],
        rows: shown,
        empty: t("dashboard.hosts.empty"),
      }),
      button(t("dashboard.hosts.open"), () => (location.hash = "#/hosts"), { size: "sm", kind: "ghost" })));
  };

  /* --------------------------- доступность -------------------------- */
  const checksHost = h("div");
  const checksCard = card(t("dashboard.card.checks"), checksHost, {
    extra: h("span", { class: "muted small" }, t("dashboard.checksHint")),
  });

  const refreshChecks = async () => {
    const result = await guard(() => api.get("/api/checks"), { quiet: true });
    if (!result || result.ok === false) return;
    const data = result.data || {};
    const items = data.items || [];
    const summary = data.summary || {};
    if (!items.length) {
      mount(checksHost, empty(t("dashboard.checks.empty"), {
        mark: "◎",
        hint: t("dashboard.checks.emptyHint"),
        extra: button(t("dashboard.checks.open"), () => (location.hash = "#/checks"), { size: "sm", kind: "ghost" }),
      }));
      return;
    }
    const order = { down: 0, warn: 1, unknown: 2, up: 3, disabled: 4 };
    const shown = [...items].sort((left, right) => (order[left.state] ?? 9) - (order[right.state] ?? 9)).slice(0, 6);
    const stateKind = { up: "ok", warn: "warn", down: "bad", unknown: "muted", disabled: "muted" };
    mount(checksHost, h("div", { class: "col" },
      h("div", { class: "chips" },
        badge(t("dashboard.checks.up", { n: summary.up || 0 }), "ok"),
        badge(t("dashboard.checks.warn", { n: summary.warn || 0 }), summary.warn ? "warn" : "muted"),
        badge(t("dashboard.checks.down", { n: summary.down || 0 }), summary.down ? "bad" : "muted")),
      h("div", {}, shown.map((item) => h("div", { class: "alert-item" },
        h("span", { class: "severity" }, badge(t(`checks.state.${item.state || "unknown"}`), stateKind[item.state] || "muted")),
        h("div", { class: "alert-body" },
          h("div", { class: "alert-title" }, item.name),
          h("div", { class: "mono small muted" }, item.target),
          item.detail ? h("div", { class: "alert-detail", title: item.detail }, item.detail) : null)))),
      button(t("dashboard.checks.open"), () => (location.hash = "#/checks"), { size: "sm", kind: "ghost" })));
  };

  /* --------------------------- диски, сеть -------------------------- */
  const diskCanvasWrap = h("div");
  const disksCard = card(t("dashboard.card.disks"), diskCanvasWrap, { class: "card" });
  const nicsCard = card(t("dashboard.card.nics"), h("div"));

  const refreshInfoBlocks = () => {
    const disks = info.disks || [];
    mount(diskCanvasWrap, table({
      columns: [
        { key: "mount", label: t("dashboard.disk.mount"), render: (row) => h("span", { class: "mono" }, row.mount) },
        { key: "device", label: t("dashboard.disk.device"), render: (row) => h("span", { class: "mono muted" }, row.device) },
        { key: "fstype", label: t("dashboard.disk.fs") },
        { key: "total", label: t("dashboard.disk.total"), align: "right", render: (row) => fmtBytes(row.total) },
        { key: "free", label: t("dashboard.disk.free"), align: "right", render: (row) => fmtBytes(row.free) },
        {
          key: "percent", label: t("dashboard.disk.used"), width: "170px",
          render: (row) => h("div", { class: "row" }, bar(row.percent / 100, { kind: row.percent >= 90 ? "bad" : row.percent >= 75 ? "warn" : "" }), h("span", { class: "num mono" }, fmtPct(row.percent))),
        },
      ],
      rows: disks,
      empty: t("dashboard.disk.empty"),
    }));

    mount(nicsCard.querySelector(".card-body"), table({
      columns: [
        { key: "name", label: t("dashboard.nic.name"), render: (row) => h("span", { class: "mono" }, row.name) },
        { key: "up", label: t("dashboard.nic.state"), render: (row) => stateBadge(row.up ? "up" : "down", row.up ? t("dashboard.nic.up") : t("dashboard.nic.down")) },
        { key: "ipv4", label: "IPv4", render: (row) => (row.ipv4 || []).join(", ") || t("common.dash") },
        { key: "mac", label: "MAC", render: (row) => h("span", { class: "mono muted" }, row.mac || t("common.dash")) },
        { key: "speed_mbps", label: t("dashboard.nic.speed"), align: "right", render: (row) => (row.speed_mbps ? t("common.mbps", { value: row.speed_mbps }) : t("common.dash")) },
      ],
      rows: info.nics || [],
      empty: t("dashboard.nic.empty"),
    }));
  };
  refreshInfoBlocks();

  /* --------------------------- процессы ----------------------------- */
  const processesHost = h("div");
  const processSearch = input({ placeholder: t("dashboard.proc.filter"), style: { maxWidth: "260px" } });
  let processSort = "cpu";
  const sortChips = h("div", { class: "chips" }, [
    ["cpu", t("dashboard.proc.byCpu")], ["mem", t("dashboard.proc.byMem")], ["name", t("dashboard.proc.byName")],
  ].map(([id, label]) =>
    h("button", {
      type: "button", class: `chip ${id === "cpu" ? "active" : ""}`,
      onclick: (event) => {
        processSort = id;
        sortChips.querySelectorAll(".chip").forEach((element) => element.classList.remove("active"));
        event.target.classList.add("active");
        refreshProcesses();
      },
    }, label)));

  const processesCard = card(t("dashboard.card.processes"), processesHost, { extra: toolbar(processSearch, sortChips) });
  let lastProcesses = [];

  const openProcess = async (pid) => {
    const result = await guard(() => api.get(`/api/processes/${pid}`), { quiet: true });
    if (!result || result.ok === false) return;
    const detail = result.data || {};
    const memory = detail.memory || {};
    const body = h("div", {},
      kvRows([
        ["PID", detail.pid], [t("dashboard.kv.name"), detail.name], [t("dashboard.kv.user"), detail.user],
        [t("dashboard.kv.status"), detail.status], [t("dashboard.kv.parent"), detail.parent], [t("dashboard.kv.threads"), detail.threads],
        [t("dashboard.kv.started"), detail.created_iso], ["Nice", detail.nice],
        ["CPU", `${fmtNumber(detail.cpu_percent)}%`],
        [t("dashboard.kv.rss"), fmtBytes(memory.rss)],
        [t("dashboard.kv.openFiles"), detail.open_files],
        [t("dashboard.kv.connections"), detail.connections],
        [t("dashboard.kv.file"), detail.exe], [t("dashboard.kv.cwd"), detail.cwd],
      ]),
      h("pre", { class: "mono-block", style: { marginTop: "10px" } }, (detail.cmdline || []).join(" ") || detail.name)
    );
    modal({
      title: t("dashboard.processTitle", { name: detail.name || detail.pid }),
      body,
      width: 720,
      actions: [
        {
          label: t("dashboard.killTerm"), kind: "danger",
          onClick: async (close) => {
            if (!(await confirmDialog(t("dashboard.confirmKill", { pid, name: detail.name })))) return;
            const result = await guard(() => api.post(`/api/processes/${pid}/kill`, {}, { force: false }), { success: t("dashboard.signalSent") });
            if (result && result.ok) close();
          },
        },
        {
          label: t("dashboard.killForce"),
          onClick: async (close) => {
            if (!(await confirmDialog(t("dashboard.confirmKillForce", { pid })))) return;
            const result = await guard(() => api.post(`/api/processes/${pid}/kill`, {}, { force: true }), { success: t("dashboard.killed") });
            if (result && result.ok) close();
          },
        },
        { label: t("common.buttons.close") },
      ],
    });
  };

  const refreshProcesses = async () => {
    const query = processSearch.value.trim();
    const result = await guard(() => api.get("/api/processes", { sort: processSort, limit: 40, q: query }), { quiet: true });
    if (!result) return;
    lastProcesses = result.items || [];
    mount(processesHost, table({
      columns: [
        { key: "pid", label: "PID", width: "70px", render: (row) => h("span", { class: "mono" }, row.pid) },
        { key: "name", label: t("dashboard.kv.name") },
        { key: "cpu", label: t("dashboard.proc.cpu"), align: "right", render: (row) => h("span", { class: "num" }, fmtNumber(row.cpu)) },
        { key: "mem_pct", label: t("dashboard.proc.mem"), align: "right", render: (row) => h("span", { class: "num" }, fmtNumber(row.mem_pct)) },
        { key: "rss_human", label: "RSS", align: "right" },
        { key: "user", label: t("dashboard.kv.user") },
        { key: "threads", label: t("dashboard.kv.threads"), align: "right" },
        { key: "cmdline", label: t("dashboard.proc.command"), render: (row) => h("span", { class: "mono muted", style: { display: "inline-block", maxWidth: "340px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom" } }, row.cmdline) },
      ],
      rows: lastProcesses,
      empty: query ? t("dashboard.proc.notFound") : t("common.defaultEmpty"),
      onRow: (row) => openProcess(row.pid),
    }));
  };

  /* --------------------------- соединения --------------------------- */
  const connectionsHost = h("div");
  const connectionsCard = card(t("dashboard.card.connections"), connectionsHost);

  const refreshConnections = async () => {
    const result = await guard(() => api.get("/api/connections", { limit: 60 }), { quiet: true });
    if (!result) return;
    const summary = result.summary || {};
    mount(connectionsHost,
      h("div", { class: "chips", style: { marginBottom: "10px" } },
        badge(t("dashboard.conn.total", { n: summary.total ?? 0 }), "muted"),
        badge(t("dashboard.conn.listening", { n: summary.listening ?? 0 }), "muted"),
        Object.entries(summary.by_status || {}).map(([status, count]) => badge(`${status} ${count}`, "neutral"))),
      summary.available === false
        ? notice(summary.error || t("dashboard.conn.needsRights"), "warn")
        : table({
            columns: [
              { key: "type", label: t("dashboard.conn.type") },
              { key: "local", label: t("dashboard.conn.local"), render: (row) => h("span", { class: "mono" }, row.local) },
              { key: "remote", label: t("dashboard.conn.remote"), render: (row) => h("span", { class: "mono" }, row.remote || t("common.dash")) },
              { key: "status", label: t("dashboard.kv.status") },
              { key: "process", label: t("dashboard.conn.process"), render: (row) => row.process ? `${row.process} (${row.pid})` : t("common.dash") },
            ],
            rows: result.items || [],
            empty: t("dashboard.conn.empty"),
          })
    );
  };

  /* --------------------------- история ------------------------------ */
  const loadHistory = async () => {
    const result = await guard(() => api.get("/api/metrics/history", { minutes: buffer.range, series: SERIES.join(",") }), { quiet: true });
    if (!result) return;
    buffer.ts = result.data.ts || [];
    buffer.series = result.data.series || {};
    buffer.loaded = true;
    redrawCharts();
  };

  const redrawCharts = () => {
    const payload = { ts: buffer.ts, series: buffer.series, labels: {
      cpu: "CPU", mem_pct: t("dashboard.legend.mem"), swap_pct: "swap", load1: t("dashboard.legend.load1"),
      net_rx: t("dashboard.legend.rx"), net_tx: t("dashboard.legend.tx"), disk_read: t("dashboard.legend.read"), disk_write: t("dashboard.legend.write"), disk_pct: t("dashboard.legend.used"),
    } };
    for (const chart of Object.values(charts)) chart.setData(payload);
  };

  const pushPoint = (fresh) => {
    if (!buffer.loaded) return;
    buffer.ts.push(fresh.ts);
    for (const key of SERIES) {
      if (!buffer.series[key]) buffer.series[key] = [];
      const value = fresh[key];
      buffer.series[key].push(typeof value === "number" && isFinite(value) ? value : null);
    }
    const limit = Math.max(120, (buffer.range * 60) / 2);
    if (buffer.ts.length > limit) {
      buffer.ts.splice(0, buffer.ts.length - limit);
      for (const key of SERIES) buffer.series[key].splice(0, buffer.series[key].length - limit);
    }
  };

  const updateLive = (fresh) => {
    if (!fresh || paused) return;
    gaugeCpu.setValue(fresh.cpu);
    gaugeMem.setValue(fresh.mem_pct, { detail: `${fmtBytes(fresh.mem_used)} / ${fmtBytes(fresh.mem_total)}` });
    gaugeDisk.setValue(fresh.disk_pct);
    gaugeSwap.setValue(fresh.swap_pct);
    cores.setData(fresh.cpu_per_core || [], (fresh.cpu_per_core || []).map((_, index) => String(index)));
    mount(statLoad, h("div", { class: "stat-label" }, "Load average"),
      h("div", { class: "stat-value" }, fmtNumber(fresh.load1, 2)),
      h("div", { class: "stat-detail" }, t("dashboard.stat.loadDetail", { l5: fmtNumber(fresh.load5, 2), l15: fmtNumber(fresh.load15, 2) })));
    mount(statProc, h("div", { class: "stat-label" }, t("dashboard.stat.processes")),
      h("div", { class: "stat-value" }, fmtNumber(fresh.proc_count, 0)),
      h("div", { class: "stat-detail" }, t("dashboard.stat.threads", { n: fmtNumber(fresh.thread_count, 0) })));
    mount(statNet, h("div", { class: "stat-label" }, t("dashboard.stat.net")),
      h("div", { class: "stat-value", style: { fontSize: "16px" } }, t("common.perSec", { value: fmtBytes(fresh.net_rx) })),
      h("div", { class: "stat-detail" }, t("dashboard.stat.tx", { value: fmtBytes(fresh.net_tx) })));
    mount(statTemp, h("div", { class: "stat-label" }, t("dashboard.stat.temp")),
      h("div", { class: "stat-value" }, fresh.temp_max === null || fresh.temp_max === undefined ? t("common.dash") : `${fmtNumber(fresh.temp_max)}°`),
      h("div", { class: "stat-detail" }, (fresh.temps || []).length ? t("dashboard.stat.sensors", { n: fresh.temps.length }) : t("dashboard.stat.noSensors")));
    mount(statMem, h("div", { class: "stat-label" }, t("dashboard.stat.mem")),
      h("div", { class: "stat-value", style: { fontSize: "16px" } }, fmtBytes(fresh.mem_used)),
      h("div", { class: "stat-detail" }, t("dashboard.stat.free", { value: fmtBytes(fresh.mem_available) })));
    const uptime = (info.host || {}).uptime;
    mount(statUptime, h("div", { class: "stat-label" }, t("dashboard.stat.system")),
      h("div", { class: "stat-value", style: { fontSize: "16px" } }, uptime ? fmtDuration(uptime) : t("common.dash")),
      h("div", { class: "stat-detail" }, (info.host || {}).os_pretty || ""));
    pushPoint(fresh);
    redrawCharts();
  };

  updateLive(point);
  await loadHistory();

  cleanups.push(bus.on("metrics", (payload) => {
    if (payload.point) updateLive(payload.point);
    if (payload.alerts) renderAlerts(payload.alerts);
  }));
  cleanups.push(poller(6000, async () => {
    if (document.hidden) return;
    await Promise.all([refreshProcesses(), refreshConnections()]);
  }));
  cleanups.push(poller(20000, refreshAgents));
  cleanups.push(poller(10000, refreshHosts));
  cleanups.push(poller(15000, refreshChecks));
  cleanups.push(poller(60000, async () => {
    const fresh = await guard(() => api.get("/api/info"), { quiet: true });
    if (fresh && fresh.data) Object.assign(info, fresh.data);
    refreshInfoBlocks();
  }, { immediate: false }));

  processSearch.addEventListener("input", () => {
    clearTimeout(processSearch._timer);
    processSearch._timer = setTimeout(refreshProcesses, 300);
  });

  const quickJump = (id) => {
    const target = root.querySelector(id);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  if (args[0] === "alerts") setTimeout(() => quickJump("#alerts"), 100);

  mount(root,
    h("div", { class: "view-head" },
      h("div", {}, h("h1", { class: "view-title" }, t("dashboard.title")),
        h("div", { class: "view-sub" }, `${host.hostname || ""} • ${host.os_pretty || ""} • ${t("dashboard.pollInterval", { ms: fmtNumber((snapshot.data.state || {}).interval_ms || 2000, 0) })}`)),
      h("div", { class: "toolbar" },
        button(t("dashboard.openJobs"), () => (location.hash = "#/jobs"), { kind: "ghost" }),
        button(t("nav.settings.title"), () => (location.hash = "#/settings"), { kind: "ghost" }))),
    alertsCard,
    gaugeCard,
    chartsCard,
    h("div", { class: "grid cards-2" }, disksCard, nicsCard),
    h("div", { class: "grid cards-2" }, processesCard, connectionsCard),
    agentsCard,
    hostsCard,
    checksCard,
    h("div", { class: "grid cards-2" }, infoCard, card(t("dashboard.card.help"), h("div", { class: "small" },
      h("p", {}, t("dashboard.help.stream")),
      h("p", {}, t("dashboard.help.thresholds")),
      h("p", {}, t("dashboard.help.local"))))));
  return () => {
    cleanups.forEach((stop) => stop && stop());
    if (processSearch._timer) clearTimeout(processSearch._timer);
  };
}
