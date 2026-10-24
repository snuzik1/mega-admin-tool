/* Kubernetes: обзор кластера, узлы, поды, деплойменты, сервисы,
   события и применение манифестов через kubectl. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, bar, button, card, chip, confirmDialog, empty, field, fmtNumber, fmtPct, fmtTime,
  guard, h, input, kvRows, modal, mount, notice, poller, select, spinner, table, tabs,
  timeAgo, toast, toolbar,
} from "../ui.js";

const INSTALL_HINT =
  "curl -LO \"https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl\"   •   sudo snap install kubectl --classic";

/* Фазы подов и состояний узлов: значение из API — ключ словаря. */
const PHASE_KEYS = {
  Running: "k8s.phase.running",
  Pending: "k8s.phase.pending",
  Succeeded: "k8s.phase.succeeded",
  Failed: "k8s.phase.failed",
  Unknown: "k8s.phase.unknown",
  Terminating: "k8s.phase.terminating",
  ContainerCreating: "k8s.phase.containerCreating",
  CrashLoopBackOff: "k8s.phase.crashLoop",
  ImagePullBackOff: "k8s.phase.imagePullBackOff",
  ErrImagePull: "k8s.phase.imagePullError",
};

function phaseBadge(phase) {
  const key = String(phase || "").toLowerCase();
  const kind = ["running", "succeeded", "completed"].includes(key)
    ? "ok"
    : ["pending", "containercreating", "terminating"].includes(key)
      ? "warn"
      : ["failed", "unknown"].includes(key) || /backoff/i.test(key)
        ? "bad"
        : "neutral";
  return badge(PHASE_KEYS[phase] ? t(PHASE_KEYS[phase]) : phase || t("common.dash"), kind);
}

function ratio(part, total) {
  if (!total || total <= 0) return 0;
  return Math.max(0, Math.min(1, part / total));
}

export async function render(root) {
  const cleanups = [];
  const cache = {};
  let status = null;
  let namespaces = [];
  let activeTab = "overview";
  let tabGeneration = 0;

  const available = () => Boolean(status && status.available);

  /* ------------------------------------------------------------------ */
  /*  Запросы                                                            */
  /* ------------------------------------------------------------------ */

  const loadStatus = async () => {
    const result = await guard(() => api.get("/api/k8s/status"), { quiet: true });
    status = (result && result.data) || null;
    return status;
  };

  const loadNamespaces = async () => {
    if (!available()) return [];
    const result = await guard(() => api.get("/api/k8s/namespaces"), { quiet: true });
    namespaces = (result && result.data) || [];
    return namespaces;
  };

  const loadResource = async (resource, force = false) => {
    if (cache[resource] && !force) return cache[resource];
    const result = await guard(() => api.get(`/api/k8s/${resource}`), { quiet: true });
    const data = result && result.ok ? result.data : null;
    cache[resource] = data;
    return data;
  };

  /* ------------------------------------------------------------------ */
  /*  Заголовок и переключатель контекста                                */
  /* ------------------------------------------------------------------ */

  const head = h("div", { class: "view-head" });
  const statusHost = h("div");
  const tabHost = h("div");
  const bodyHost = h("div");
  const tabBar = tabs(
    [
      { id: "overview", label: t("k8s.tab.overview") },
      { id: "nodes", label: t("k8s.tab.nodes") },
      { id: "pods", label: t("k8s.tab.pods") },
      { id: "deployments", label: t("k8s.tab.deployments") },
      { id: "services", label: t("k8s.tab.services") },
      { id: "events", label: t("k8s.tab.events") },
      { id: "apply", label: t("k8s.tab.apply") },
    ],
    { active: "overview", onChange: (id) => { activeTab = id; drawTab(); } }
  );

  const reloadAll = async (force = true) => {
    await loadStatus();
    if (available()) {
      await loadNamespaces();
      Object.keys(cache).forEach((key) => delete cache[key]);
      await drawTab(force);
    }
    drawStatus();
    drawHeadTools();
  };

  /* ------------------------------------------------------------------ */
  /*  Состояние kubectl и целевой контекст                               */
  /* ------------------------------------------------------------------ */

  const contextSelect = select([], { value: (status && status.context) || "" });
  const namespaceSelect = select([], { value: (status && status.namespace) || "" });
  const statusBody = h("div");

  const applyTarget = async () => {
    const context = contextSelect.value;
    const namespace = namespaceSelect.value;
    const result = await guard(
      () => api.post("/api/k8s/target", { context, namespace }),
      {
        success: t("k8s.context.switched", {
          context: context || t("k8s.context.defaultShort"),
          namespace: namespace || t("k8s.namespace.all"),
        }),
      }
    );
    if (result && result.ok) {
      if (status) {
        status.context = result.context !== undefined ? result.context : context;
        status.namespace = result.namespace !== undefined ? result.namespace : namespace;
      }
      Object.keys(cache).forEach((key) => delete cache[key]);
      await loadNamespaces();
      drawStatus();
      drawTab(true);
    }
  };

  const drawStatus = () => {
    const state = status || {};
    if (!state.available) {
      mount(statusBody,
        notice(state.error || t("k8s.status.notFound"), "bad"),
        h("p", { class: "small muted" }, t("k8s.status.install", { hint: INSTALL_HINT })),
        h("p", { class: "small muted" }, t("k8s.status.pathHint"))
      );
      mount(statusHost, card(t("k8s.card.status"), statusBody, {
        extra: toolbar(button(t("k8s.status.recheck"), async () => { await reloadAll(); }, { size: "sm", kind: "ghost" })),
      }));
      return;
    }

    const contexts = (state.contexts && state.contexts.items) || [];
    mount(contextSelect,
      h("option", { value: "" }, t("k8s.context.defaultOption")),
      ...contexts.map((item) => h("option", { value: item }, item)));
    if (state.context && !contexts.includes(state.context)) contextSelect.append(h("option", { value: state.context }, state.context));
    contextSelect.value = state.context || "";

    const nsOptions = [""].concat(namespaces);
    mount(namespaceSelect, ...nsOptions.map((item) => h("option", { value: item }, item || t("k8s.namespace.all"))));
    namespaceSelect.value = state.namespace || "";

    const current = (state.contexts && state.contexts.current) || state.context || t("common.dash");
    mount(statusBody,
      h("div", { class: "grid cards-2" },
        kvRows([
          ["kubectl", (state.version || "").split("\n")[0] || t("common.dash")],
          [t("k8s.kv.defaultContext"), current],
          [t("k8s.kv.currentContext"), state.context || t("common.dash")],
          [t("k8s.kv.namespace"), state.namespace || t("k8s.namespace.allShort")],
        ]),
        h("div", { class: "col" },
          field(t("k8s.context.switchField"), contextSelect),
          field(t("k8s.kv.namespace"), namespaceSelect),
          h("div", { class: "toolbar" }, button(t("common.buttons.apply"), applyTarget, { kind: "primary", size: "sm" }))
        )
      ),
      state.error ? h("p", { class: "small muted" }, state.error) : null
    );
    mount(statusHost, card(t("k8s.card.status"), statusBody, {
      extra: toolbar(button(t("common.buttons.refresh"), async () => { await reloadAll(); }, { size: "sm", kind: "ghost" })),
    }));
  };

  /* ------------------------------------------------------------------ */
  /*  Кнопки в шапке                                                     */
  /* ------------------------------------------------------------------ */

  const headTools = h("div", { class: "toolbar" });
  const drawHeadTools = () => {
    mount(headTools,
      button(t("k8s.tab.overview"), () => { activeTab = "overview"; tabBar.setActive("overview"); drawTab(); }, { kind: "ghost" }),
      button(t("k8s.context.listBtn"), async () => {
        const result = await guard(() => api.get("/api/k8s/contexts"), { quiet: true });
        const items = (result && result.ok && result.data && result.data.items) || [];
        modal({
          title: t("k8s.context.title"),
          width: 640,
          body: items.length
            ? h("ul", { class: "list-clean" }, items.map((name) =>
                h("li", {}, h("span", { class: "mono" }, name), name === (status && status.context) ? badge(t("k8s.context.current"), "ok") : null)))
            : empty(t("k8s.context.empty")),
          actions: [{ label: t("common.buttons.close") }],
        });
      }, { kind: "ghost" }),
      button(t("k8s.apply.title"), () => { activeTab = "apply"; tabBar.setActive("apply"); drawTab(); }, { kind: "primary" }),
      button(t("nav.settings.title"), () => (location.hash = "#/settings"), { kind: "ghost" })
    );
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладки                                                            */
  /* ------------------------------------------------------------------ */

  const drawTab = async (force = false) => {
    if (!available()) {
      mount(bodyHost, empty(t("k8s.unavailable"), { mark: "☸", hint: t("k8s.unavailableHint") }));
      return;
    }
    // Защита от гонки: медленная отрисовка старой вкладки не должна
    // перезатирать содержимое вкладки, выбранной позже.
    const generation = (tabGeneration += 1);
    const host = h("div", { class: "col" });
    mount(bodyHost, spinner());
    if (activeTab === "overview") await drawOverview(host, force);
    else if (activeTab === "nodes") await drawNodes(host, force);
    else if (activeTab === "pods") await drawPods(host, force);
    else if (activeTab === "deployments") await drawDeployments(host, force);
    else if (activeTab === "services") await drawServices(host, force);
    else if (activeTab === "events") await drawEvents(host, force);
    else if (activeTab === "apply") await drawApply(host);
    if (generation === tabGeneration) mount(bodyHost, host);
  };

  /* -------------------------- Обзор --------------------------------- */

  const drawOverview = async (host, force) => {
    const data = force ? await loadResource("overview", true) : await loadResource("overview");
    if (!data) {
      mount(host, empty(t("k8s.errors.overview")));
      return;
    }
    const counts = data.counts || {};
    const phases = counts.pods_by_phase || {};
    const tiles = [
      [t("k8s.tab.nodes"), counts.nodes || 0, t("k8s.tile.ready", { n: counts.nodes_ready || 0 })],
      [t("k8s.tab.pods"), counts.pods || 0, t("k8s.tile.podsProblem", { n: counts.pods_problem || 0 })],
      [t("k8s.tab.deployments"), counts.deployments || 0, t("k8s.tile.ready", { n: counts.deployments_ready || 0 })],
      [t("k8s.tab.services"), counts.services || 0, ""],
      [t("k8s.tile.warnings"), counts.warnings || 0, t("k8s.tile.warningsDetail")],
      [t("k8s.kv.namespace"), data.namespace || t("k8s.namespace.allShort"), data.context || ""],
    ];

    mount(host,
      card(t("k8s.card.summary"), h("div", {},
        h("div", { class: "grid cards-3" }, tiles.map(([label, value, sub]) =>
          h("div", { class: "stat" },
            h("div", { class: "stat-label" }, label),
            h("div", { class: "stat-value" }, typeof value === "number" ? fmtNumber(value, 0) : value),
            sub ? h("div", { class: "stat-detail" }, sub) : null))),
        Object.keys(phases).length
          ? h("div", { class: "chips", style: { marginTop: "12px" } }, Object.entries(phases).map(([phase, count]) =>
              h("span", { class: "tag" }, phaseBadge(phase), h("span", {}, ` ${count}`))))
          : null
      ), { extra: toolbar(button(t("common.buttons.refresh"), () => drawOverview(host, true), { size: "sm", kind: "ghost" })) }),

      card(t("k8s.card.top"), (data.top_nodes || []).length
        ? table({
            columns: [
              { key: "name", label: t("k8s.col.node") },
              { key: "cpu", label: "CPU", render: (row) => row.cpu || t("common.dash") },
              { key: "cpu_pct", label: "CPU %", align: "right", render: (row) => row.cpu_pct ? fmtPct(row.cpu_pct) : t("common.dash") },
              { key: "mem", label: t("k8s.col.memory"), render: (row) => row.mem || t("common.dash") },
              { key: "mem_bar", label: t("k8s.col.memoryPct"), width: "180px", render: (row) => row.mem_pct ? h("div", {}, bar(row.mem_pct / 100), h("span", { class: "small muted" }, fmtPct(row.mem_pct))) : t("common.dash") },
            ],
            rows: data.top_nodes,
          })
        : notice(t("k8s.top.unavailable"), "warn"),
        { extra: toolbar(button(t("common.buttons.refresh"), () => drawOverview(host, true), { size: "sm", kind: "ghost" })) }),

      card(t("k8s.tab.nodes"), table({
        columns: [
          { key: "name", label: t("k8s.col.node") },
          { key: "roles", label: t("k8s.col.roles"), render: (row) => h("span", {}, (row.roles || []).join(", ")) },
          { key: "ready", label: t("k8s.col.state"), render: (row) => statusCell(row) },
          { key: "kubelet", label: "kubelet" },
          { key: "cpu_cores", label: "CPU", align: "right", render: (row) => row.cpu_cores ? t("k8s.cores", { n: fmtNumber(row.cpu_cores, 0) }) : t("common.dash") },
          { key: "mem_total_human", label: t("k8s.col.memory"), align: "right" },
          { key: "internal_ip", label: "IP" },
        ],
        rows: data.nodes || [],
        empty: t("k8s.empty.nodes"),
      }))
    );
  };

  const statusCell = (node) => {
    if (node.ready) return badge(t("k8s.node.ready"), "ok");
    if (node.unschedulable) return badge(t("k8s.node.schedulingDisabled"), "warn");
    return badge(t("k8s.node.notReady"), "bad");
  };

  /* --------------------------- Узлы --------------------------------- */

  const drawNodes = async (host, force) => {
    const [nodes, top] = await Promise.all([loadResource("nodes", force), loadResource("top", force)]);
    if (!nodes) {
      mount(host, empty(t("k8s.errors.nodes")));
      return;
    }
    const usage = {};
    for (const item of (top && top.nodes) || []) usage[item.name] = item;

    const detail = (node) => {
      modal({
        title: t("k8s.node.title", { name: node.name }),
        width: 720,
        body: h("div", {},
          kvRows([
            [t("k8s.col.roles"), (node.roles || []).join(", ")],
            [t("k8s.kv.ready"), node.ready ? t("common.buttons.yes") : t("common.buttons.no")],
            [t("k8s.kv.scheduling"), node.unschedulable ? t("k8s.state.disabled") : t("k8s.state.enabled")],
            ["IP", node.internal_ip],
            [t("k8s.kv.os"), node.os_image],
            [t("k8s.kv.kernelArch"), node.arch],
            ["kubelet", node.kubelet],
            [t("k8s.kv.runtime"), node.runtime],
            ["CPU", t("k8s.cores", { n: fmtNumber(node.cpu_cores, 0) })],
            [t("k8s.col.memory"), node.mem_total_human],
            [t("k8s.kv.podsLimit"), node.pods_capacity],
            [t("k8s.kv.created"), fmtTime(node.created)],
            ["Taints", (node.taints || []).join(", ")],
            [t("k8s.kv.pressure"), Object.entries(node.pressure || {}).filter(([, value]) => value).map(([key]) => key).join(", ")],
          ]),
          usage[node.name]
            ? h("div", { style: { marginTop: "12px" } }, h("h4", {}, t("k8s.kv.usage")),
                kvRows([["CPU", usage[node.name].cpu], ["CPU %", fmtPct(usage[node.name].cpu_pct)], [t("k8s.col.memory"), usage[node.name].mem]]))
            : null
        ),
        actions: [{ label: t("common.buttons.close") }],
      });
    };

    mount(host, card(t("k8s.card.nodes"), table({
      columns: [
        { key: "name", label: t("k8s.col.node"), render: (row) => h("span", { class: "mono" }, row.name) },
        { key: "roles", label: t("k8s.col.roles"), render: (row) => (row.roles || []).join(", ") },
        { key: "ready", label: t("k8s.col.ready"), render: statusCell },
        { key: "internal_ip", label: "IP" },
        { key: "kubelet", label: "kubelet" },
        { key: "cpu_cores", label: "CPU", align: "right", render: (row) => row.cpu_cores ? fmtNumber(row.cpu_cores, 0) : t("common.dash") },
        { key: "mem_total_human", label: t("k8s.col.memory"), align: "right" },
        { key: "cpu_use", label: "CPU %", align: "right", render: (row) => usage[row.name] ? fmtPct(usage[row.name].cpu_pct) : t("common.dash") },
        { key: "mem_use", label: t("k8s.col.memoryPct"), align: "right", render: (row) => usage[row.name] ? fmtPct(usage[row.name].mem_pct) : t("common.dash") },
      ],
      rows: nodes,
      empty: t("k8s.empty.nodes"),
      onRow: detail,
    }), { extra: toolbar(button(t("common.buttons.refresh"), () => drawNodes(host, true), { size: "sm", kind: "ghost" })) }));
  };

  /* --------------------------- Поды --------------------------------- */

  const podActions = (pod) => {
    modal({
      title: t("k8s.pod.title", { ns: pod.namespace, name: pod.name }),
      width: 860,
      body: h("div", {},
        kvRows([
          [t("k8s.kv.namespace"), pod.namespace],
          [t("k8s.col.node"), pod.node],
          [t("k8s.kv.phase"), phaseBadge(pod.phase)],
          [t("k8s.col.ready"), pod.ready],
          [t("k8s.col.restarts"), pod.restarts],
          [t("k8s.col.reason"), pod.reason],
          [t("k8s.kv.owner"), pod.owner],
          [t("k8s.kv.started"), fmtTime(pod.started)],
          [t("k8s.kv.images"), (pod.images || []).join(", ")],
        ])
      ),
      actions: [
        { label: t("k8s.pod.logs"), kind: "primary", onClick: (close) => { close(); loadLogsInto(pod, "", 300); } },
        { label: t("k8s.action.describe"), onClick: (close) => { close(); openDescribe("pod", pod.name, pod.namespace); } },
        {
          label: t("common.buttons.delete"),
          kind: "danger",
          onClick: async (close) => {
            close();
            const ok = await confirmDialog(
              t("k8s.pod.confirmDelete", { ns: pod.namespace, name: pod.name }),
              { title: t("k8s.pod.deleteTitle"), okLabel: t("common.buttons.delete") });
            if (!ok) return;
            const result = await guard(
              () => api.post("/api/k8s/delete", { kind: "pod", name: pod.name, namespace: pod.namespace }),
              { success: t("k8s.pod.deleteSent") }
            );
            if (result && result.ok) drawTab(true);
          },
        },
        { label: t("common.buttons.close") },
      ],
    });
  };

  /** Определяет имена контейнеров пода по выводу `kubectl describe`. */
  const podContainers = async (pod) => {
    const result = await guard(() => api.get("/api/k8s/describe", { kind: "pod", name: pod.name, namespace: pod.namespace }), { quiet: true });
    if (!result || result.ok === false || !result.text) return [];
    const text = String(result.text);
    const block = text.split(/^Containers:$/m)[1] || "";
    const names = [];
    for (const line of block.split("\n")) {
      const match = /^ {2}([a-z0-9][\w.-]*):\s*$/.exec(line);
      if (match) names.push(match[1]);
      if (/^(Conditions|Volumes|QoS Class|Node|Events):/.test(line)) break;
    }
    return names;
  };

  const loadLogsInto = async (pod, container, tail) => {
    const body = h("pre", { class: "console", style: { height: "440px" } }, t("common.loading"));
    const containerSelect = select([{ value: "", label: container || t("k8s.logs.allContainers") }], { value: container });
    const containerField = h("div", { class: "grow", hidden: Boolean(container) });
    const title = t("k8s.logs.title", { pod: `${pod.namespace}/${pod.name}` }) + (container ? ` · ${container}` : "");

    const reload = async (name = containerSelect.value) => {
      body.textContent = t("common.loading");
      const params = { pod: pod.name, namespace: pod.namespace, tail };
      if (name) params.container = name;
      const result = await guard(() => api.get("/api/k8s/logs", params), { quiet: true });
      if (!result) {
        body.textContent = t("k8s.logs.failed");
        return;
      }
      if (result.ok === false && result.error) {
        body.textContent = t("k8s.logs.error", { error: result.error });
        return;
      }
      body.textContent = result.text || t("k8s.noOutput");
    };

    modal({
      title,
      width: 940,
      body: h("div", {},
        h("div", { class: "toolbar" },
          button(t("k8s.logs.tailLines", { n: 50 }), () => { tail = 50; reload(); }, { size: "sm", kind: "ghost" }),
          button(t("k8s.logs.tailLines", { n: 300 }), () => { tail = 300; reload(); }, { size: "sm", kind: "ghost" }),
          button(t("k8s.logs.all"), () => { tail = 5000; reload(); }, { size: "sm", kind: "ghost" }),
          containerSelect),
        containerField,
        body
      ),
      actions: [
        { label: t("common.buttons.download"), onClick: () => downloadText(`${pod.namespace}-${pod.name}.log`, body.textContent) },
        { label: t("common.buttons.close") },
      ],
    });

    const names = await podContainers(pod);
    if (names.length > 1) {
      mount(containerSelect, h("option", { value: "" }, t("k8s.logs.oneContainer")),
        ...names.map((name) => h("option", { value: name }, name)));
      containerSelect.value = container || names[0];
      containerField.hidden = false;
      containerSelect.onchange = () => reload(containerSelect.value);
    }
    await reload(containerSelect.value);
  };

  const openDescribe = async (kind, name, namespace) => {
    const pre = h("pre", { class: "console", style: { height: "460px" } }, t("common.loading"));
    modal({
      title: t("k8s.describe.title", { kind, name }),
      width: 940,
      body: pre,
      actions: [
        { label: t("common.buttons.download"), onClick: () => downloadText(`${kind}-${name}.txt`, pre.textContent) },
        { label: t("common.buttons.close") },
      ],
    });
    const result = await guard(() => api.get("/api/k8s/describe", { kind, name, namespace }), { quiet: true });
    pre.textContent = (result && (result.text || result.error)) || t("k8s.noOutput");
  };

  const drawPods = async (host, force) => {
    const pods = await loadResource("pods", force);
    if (!pods) {
      mount(host, empty(t("k8s.errors.pods")));
      return;
    }
    const problem = pods.filter((pod) => !["Running", "Succeeded"].includes(pod.phase) || pod.restarts > 5).length;
    mount(host,
      h("div", { class: "chips" },
        chip(t("k8s.chip.total", { n: pods.length }), { active: true }),
        chip(t("k8s.chip.problemPods", { n: problem })),
        chip(t("k8s.chip.namespaces", { n: new Set(pods.map((pod) => pod.namespace)).size }))),
      card(t("k8s.tab.pods"), table({
        columns: [
          { key: "namespace", label: "Namespace" },
          { key: "name", label: t("k8s.col.pod"), render: (row) => h("span", { class: "mono" }, row.name) },
          { key: "phase", label: t("k8s.kv.phase"), render: (row) => phaseBadge(row.phase) },
          { key: "ready", label: t("k8s.col.readyShort"), align: "right" },
          { key: "restarts", label: t("k8s.col.restarts"), align: "right", render: (row) => row.restarts > 5 ? badge(row.restarts, "warn") : row.restarts },
          { key: "reason", label: t("k8s.col.reason") },
          { key: "node", label: t("k8s.col.node") },
          { key: "age", label: t("k8s.col.age"), render: (row) => timeAgo(row.started) },
        ],
        rows: pods,
        empty: t("k8s.empty.pods"),
        onRow: podActions,
      }), { extra: toolbar(button(t("common.buttons.refresh"), () => drawPods(host, true), { size: "sm", kind: "ghost" })) })
    );
  };

  /* ---------------------- Развёртывания ------------------------------ */

  const scaleDialog = (deployment) => {
    const replicas = input({ type: "number", min: "0", max: "500", value: String(deployment.desired) });
    modal({
      title: t("k8s.scale.title", { ns: deployment.namespace, name: deployment.name }),
      width: 420,
      body: h("div", {}, field(t("k8s.scale.replicas"), replicas, t("k8s.scale.current", { n: deployment.desired }))),
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.apply"),
          kind: "primary",
          onClick: async (close) => {
            const result = await guard(
              () => api.post("/api/k8s/scale", {
                kind: "deployment",
                name: deployment.name,
                namespace: deployment.namespace,
                replicas: Number(replicas.value),
              }),
              { success: t("k8s.scale.done") }
            );
            if (result && result.ok) {
              close();
              drawTab(true);
            }
          },
        },
      ],
    });
  };

  const drawDeployments = async (host, force) => {
    const items = await loadResource("deployments", force);
    if (!items) {
      mount(host, empty(t("k8s.errors.deployments")));
      return;
    }
    const notReady = items.filter((item) => item.ready !== item.desired).length;
    mount(host,
      h("div", { class: "chips" },
        chip(t("k8s.chip.total", { n: items.length }), { active: true }),
        chip(t("k8s.chip.notReady", { n: notReady })),
        chip(t("k8s.chip.replicas", { n: items.reduce((sum, item) => sum + item.desired, 0) }))),
      card(t("k8s.tab.deployments"), table({
        columns: [
          { key: "namespace", label: "Namespace" },
          { key: "name", label: t("k8s.col.name"), render: (row) => h("span", { class: "mono" }, row.name) },
          { key: "ready_ratio", label: t("k8s.col.ready"), width: "180px", sortValue: (row) => ratio(row.ready, row.desired), render: (row) =>
              h("div", {},
                h("div", { class: "small" }, `${row.ready}/${row.desired}`),
                bar(ratio(row.ready, row.desired), { label: t("k8s.deploy.readyOf", { ready: row.ready, desired: row.desired }) })) },
          { key: "updated", label: t("k8s.col.updated"), align: "right" },
          { key: "available", label: t("k8s.col.available"), align: "right" },
          { key: "images", label: t("k8s.col.image"), render: (row) => h("span", { class: "mono small" }, (row.images || [])[0] || t("common.dash")) },
          { key: "created", label: t("k8s.col.created"), render: (row) => timeAgo(row.created) },
          { key: "actions", label: "", sortable: false, render: (row) =>
              h("div", { class: "toolbar" },
                button(t("k8s.action.scale"), () => scaleDialog(row), { size: "sm", kind: "ghost" }),
                button(t("k8s.action.restart"), async () => {
                  const ok = await confirmDialog(
                    t("k8s.deploy.confirmRestart", { ns: row.namespace, name: row.name }),
                    { title: t("k8s.action.restart"), okLabel: t("common.buttons.restart"), danger: false });
                  if (!ok) return;
                  await guard(() => api.post("/api/k8s/restart", { kind: "deployment", name: row.name, namespace: row.namespace }), { success: t("k8s.deploy.restartStarted") });
                  drawTab(true);
                }, { size: "sm", kind: "ghost" }),
                button(t("k8s.action.rollback"), async () => {
                  const ok = await confirmDialog(
                    t("k8s.deploy.confirmUndo", { ns: row.namespace, name: row.name }),
                    { title: t("k8s.action.rollback"), okLabel: t("k8s.action.rollbackConfirm"), danger: false });
                  if (!ok) return;
                  await guard(() => api.post("/api/k8s/undo", { kind: "deployment", name: row.name, namespace: row.namespace }), { success: t("k8s.deploy.undoStarted") });
                  drawTab(true);
                }, { size: "sm", kind: "ghost" }),
                button(t("common.buttons.delete"), async () => {
                  const ok = await confirmDialog(
                    t("k8s.deploy.confirmDelete", { ns: row.namespace, name: row.name }),
                    { title: t("k8s.deploy.deleteTitle"), okLabel: t("common.buttons.delete") });
                  if (!ok) return;
                  await guard(() => api.post("/api/k8s/delete", { kind: "deployment", name: row.name, namespace: row.namespace }), { success: t("k8s.deploy.deleteSent") });
                  drawTab(true);
                }, { size: "sm", kind: "danger" })) },
        ],
        rows: items,
        empty: t("k8s.empty.deployments"),
        onRow: (row) => openDescribe("deployment", row.name, row.namespace),
      }), { extra: toolbar(button(t("common.buttons.refresh"), () => drawDeployments(host, true), { size: "sm", kind: "ghost" })) })
    );
  };

  /* --------------------------- Сервисы ------------------------------- */

  const drawServices = async (host, force) => {
    const items = await loadResource("services", force);
    if (!items) {
      mount(host, empty(t("k8s.errors.services")));
      return;
    }
    mount(host, card(t("k8s.tab.services"), table({
      columns: [
        { key: "namespace", label: "Namespace" },
        { key: "name", label: t("k8s.col.name"), render: (row) => h("span", { class: "mono" }, row.name) },
        { key: "type", label: t("k8s.col.type"), render: (row) => badge(row.type, row.type === "LoadBalancer" ? "ok" : row.type === "NodePort" ? "warn" : "muted") },
        { key: "cluster_ip", label: "Cluster IP" },
        { key: "external", label: t("k8s.col.external"), render: (row) => (row.external || []).length ? (row.external || []).join(", ") : t("common.dash") },
        { key: "ports", label: t("k8s.col.ports"), render: (row) => h("span", { class: "mono small" }, (row.ports || []).join(", ") || t("common.dash")) },
        { key: "selector", label: t("k8s.col.selector"), render: (row) => h("span", { class: "mono small" }, Object.entries(row.selector || {}).map(([key, value]) => `${key}=${value}`).join(", ") || t("common.dash")) },
      ],
      rows: items,
      empty: t("k8s.empty.services"),
      onRow: (row) => openDescribe("service", row.name, row.namespace),
    }), { extra: toolbar(button(t("common.buttons.refresh"), () => drawServices(host, true), { size: "sm", kind: "ghost" })) }));
  };

  /* -------------------------- События -------------------------------- */

  const drawEvents = async (host, force) => {
    const items = await loadResource("events", force);
    if (!items) {
      mount(host, empty(t("k8s.errors.events")));
      return;
    }
    const warnings = items.filter((item) => item.type === "Warning").length;
    mount(host,
      h("div", { class: "chips" },
        chip(t("k8s.chip.total", { n: items.length }), { active: true }),
        chip(`Warning: ${warnings}`),
        chip(`Normal: ${items.length - warnings}`)),
      card(t("k8s.card.events"), table({
        columns: [
          { key: "time", label: t("k8s.col.time"), render: (row) => fmtTime(row.time) },
          { key: "type", label: t("k8s.col.type"), sortable: true, render: (row) => badge(row.type, row.type === "Warning" ? "warn" : "muted") },
          { key: "reason", label: t("k8s.col.reason") },
          { key: "kind", label: t("k8s.col.object"), render: (row) => h("span", { class: "mono small" }, `${row.kind}/${row.object}`) },
          { key: "namespace", label: "Namespace" },
          { key: "count", label: t("k8s.col.count"), align: "right" },
          { key: "message", label: t("k8s.col.message"), render: (row) => row.message },
        ],
        rows: items,
        empty: t("k8s.empty.events"),
      }), { extra: toolbar(button(t("common.buttons.refresh"), () => drawEvents(host, true), { size: "sm", kind: "ghost" })) })
    );
  };

  /* ------------------------- Манифесты ------------------------------- */

  const drawApply = async (host) => {
    const fileInput = input({ placeholder: t("k8s.apply.pathPlaceholder") });
    const dryRun = h("input", { type: "checkbox", checked: true });
    const output = h("pre", { class: "console", style: { height: "260px" }, hidden: true });

    const run = async (action) => {
      const path = fileInput.value.trim();
      if (!path) {
        toast(t("k8s.apply.needPath"), "warn");
        return;
      }
      output.hidden = false;
      output.textContent = t("k8s.apply.running");
      const endpoint = action === "delete-file" ? "/api/k8s/delete-file" : "/api/k8s/apply";
      const result = await guard(() => api.post(endpoint, { path, dry_run: dryRun.checked }), { quiet: true });
      if (!result) {
        output.textContent = t("common.toast.requestFailed");
        return;
      }
      output.textContent = result.text || result.out || result.error || (result.ok ? t("k8s.apply.done") : t("k8s.apply.failed"));
      toast(result.ok ? t("k8s.apply.ok") : (result.error || t("k8s.apply.error")), result.ok ? "success" : "error");
      Object.keys(cache).forEach((key) => delete cache[key]);
    };

    mount(host,
      card(t("k8s.apply.title"), h("div", {},
        h("div", { class: "grid grid-2" },
          field(t("k8s.apply.pathLabel"), fileInput, t("k8s.apply.pathHint")),
          h("div", { class: "col" },
            h("label", { class: "check" }, dryRun, h("span", {}, t("k8s.apply.dryRun"))),
            toolbar(
              button(t("common.buttons.apply"), () => run("apply"), { kind: "primary" }),
              button(t("k8s.apply.deleteFile"), () => run("delete-file"), { kind: "danger" }),
              button(t("k8s.apply.clearButton"), () => { output.textContent = ""; output.hidden = true; }, { kind: "ghost" })
            ))),
        output
      )),
      card(t("k8s.apply.tips"), h("div", { class: "small muted" },
        h("p", {}, t("k8s.apply.tip1")),
        h("p", {}, t("k8s.apply.tip2")),
        h("p", {}, t("k8s.apply.tip3"))))
    );
  };

  /* ------------------------------------------------------------------ */
  /*  Служебное                                                          */
  /* ------------------------------------------------------------------ */

  const downloadText = (filename, text) => {
    const blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  /* ------------------------------------------------------------------ */
  /*  Старт                                                              */
  /* ------------------------------------------------------------------ */

  mount(root, head, statusHost, h("div", { class: "col" }, tabBar, bodyHost));
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, "Kubernetes"),
      h("div", { class: "view-sub" }, t("k8s.subtitle"))),
    headTools);

  await loadStatus();
  drawHeadTools();
  if (available()) await loadNamespaces();
  drawStatus();
  await drawTab();

  cleanups.push(poller(15000, () => {
    if (document.hidden || !available()) return null;
    if (activeTab === "overview" || activeTab === "pods" || activeTab === "events") return drawTab(true);
    return null;
  }, { immediate: false }));

  return () => cleanups.forEach((stop) => stop && stop());
}
