/* Задачи MegaTool: запуски Ansible, сборка конфигов, применения манифестов.
   Живой лог открывается по WebSocket. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, card, chip, confirmDialog, empty, fmtNumber, fmtTime, guard, h, jobModal,
  kvRows, modal, mount, poller, select, spinner, stateBadge, table, tabs, timeAgo, toolbar,
} from "../ui.js";

/* Подписи статусов берём из общих строк; названия технологий не переводим. */
const STATUS_KEYS = {
  running: "common.status.running",
  success: "common.status.success",
  failed: "common.status.failed",
  cancelled: "common.status.cancelled",
  pending: "common.status.pending",
};

const KIND_LABEL = {
  ansible: "Ansible",
  ansible_ping: "Ansible ping",
  kubectl: "kubectl",
  compose: "Compose",
  nginx: "nginx",
};

const statusLabel = (status) => (STATUS_KEYS[status] ? t(STATUS_KEYS[status]) : status);

const kindLabel = (kind) => {
  if (kind === "generic") return t("jobs.kind.generic");
  if (kind === "command") return t("jobs.kind.command");
  return KIND_LABEL[kind] || kind || t("common.dash");
};

export async function render(root) {
  const cleanups = [];
  let items = [];
  let running = 0;
  let kindFilter = "";
  let activeTab = "all";

  const head = h("div", { class: "view-head" });
  const host = h("div", { class: "col" });
  const filterBar = h("div", { class: "chips" });
  const tableHost = h("div");

  const load = async () => {
    const result = await guard(() => api.get("/api/jobs", { limit: 100, kind: kindFilter || undefined }), { quiet: true });
    if (result) {
      items = result.items || [];
      running = result.running || 0;
    }
    drawFilterBar();
    drawTable();
  };

  const kinds = () => [...new Set(items.map((item) => item.kind).filter(Boolean))].sort();

  const drawFilterBar = () => {
    const tabsHost = tabs(
      [
        { id: "all", label: t("jobs.tabs.all"), count: items.length },
        { id: "running", label: t("jobs.tabs.running"), count: running },
        { id: "failed", label: t("jobs.tabs.failed"), count: items.filter((item) => item.status === "failed").length },
      ],
      {
        active: activeTab,
        onChange: (id) => { activeTab = id; drawFilterBar(); drawTable(); },
      }
    );
    const kindSelect = select(
      [{ value: "", label: t("jobs.allKinds") }].concat(kinds().map((kind) => ({ value: kind, label: kindLabel(kind) }))),
      { value: kindFilter, onchange: (event) => { kindFilter = event.target.value; load(); } }
    );
    mount(filterBar,
      h("div", { class: "toolbar" }, tabsHost, kindSelect,
        button(t("jobs.cancelAll"), async () => {
          const ok = await confirmDialog(t("jobs.confirmCancelAll", { n: running }), { title: t("jobs.cancelTitle"), okLabel: t("common.buttons.stop") });
          if (!ok) return;
          await guard(() => api.post("/api/jobs/cancel-all"), { success: t("jobs.allStopped") });
          load();
        }, { kind: "danger", size: "sm", disabled: !running }),
        button(t("common.buttons.refresh"), () => load(), { kind: "ghost", size: "sm" })),
      h("div", { class: "small muted" }, t("jobs.liveHint")));
  };

  const visible = () => {
    if (activeTab === "running") return items.filter((item) => item.status === "running" || item.status === "pending");
    if (activeTab === "failed") return items.filter((item) => item.status === "failed");
    return items;
  };

  const kindBadge = (kind) => badge(kindLabel(kind), kind === "ansible" ? "neutral" : kind === "kubectl" ? "neutral" : "muted");

  const drawTable = () => {
    const rows = visible();
    mount(tableHost, card(t("jobs.card.log"), rows.length
      ? table({
          columns: [
            { key: "status", label: t("jobs.col.status"), render: (row) => stateBadge(row.status, statusLabel(row.status)) },
            { key: "title", label: t("jobs.col.title"), render: (row) => h("span", {}, row.title || row.id) },
            { key: "kind", label: t("jobs.col.kind"), render: (row) => kindBadge(row.kind) },
            { key: "created", label: t("jobs.col.created"), render: (row) => timeAgo(row.created) },
            { key: "duration_human", label: t("jobs.col.duration"), align: "right", render: (row) => row.duration_human || t("common.dash") },
            { key: "exit_code", label: t("jobs.col.exitCode"), align: "right", render: (row) => row.exit_code === null || row.exit_code === undefined ? t("common.dash") : row.exit_code },
            { key: "lines", label: t("jobs.col.lines"), align: "right" },
            { key: "actions", label: "", sortable: false, render: (row) =>
                h("div", { class: "toolbar" },
                  button(t("jobs.openLog"), () => openLog(row), { size: "sm", kind: "ghost" }),
                  row.status === "running"
                    ? button(t("common.job.cancel"), async () => {
                        const ok = await confirmDialog(t("jobs.confirmCancel", { title: row.title || row.id }), { title: t("jobs.cancelOneTitle"), okLabel: t("common.buttons.stop") });
                        if (!ok) return;
                        await guard(() => api.post(`/api/jobs/${row.id}/cancel`), { success: t("common.console.cancelRequested") });
                        load();
                      }, { size: "sm", kind: "danger" })
                    : button(t("common.buttons.details"), () => openDetail(row), { size: "sm", kind: "ghost" })) },
          ],
          rows,
          empty: t("jobs.noJobsFound"),
          onRow: (row) => openLog(row),
        })
      : empty(t("jobs.noJobs"), { mark: "✦", hint: t("jobs.noJobsHint") }),
      { extra: toolbar(h("span", { class: "small muted" }, t("jobs.counts", { total: fmtNumber(items.length, 0), running: fmtNumber(running, 0) }))) }));
  };

  const openLog = async (job) => {
    jobModal(job, { title: t("common.job.title", { title: job.title || job.id }) });
    // Подтягиваем статус задачи, пока открыт журнал.
    const stop = poller(4000, () => (document.hidden || !document.querySelector(".overlay") ? null : load()), { immediate: false });
    cleanups.push(stop);
  };

  const openDetail = async (job) => {
    const detail = await guard(() => api.get(`/api/jobs/${job.id}`), { quiet: true });
    const full = (detail && detail.job) || job;
    const rows = [
      [t("jobs.kv.id"), h("span", { class: "mono" }, full.id)],
      [t("jobs.kv.title"), full.title || t("common.dash")],
      [t("jobs.col.kind"), kindLabel(full.kind)],
      [t("jobs.col.status"), stateBadge(full.status, statusLabel(full.status))],
      [t("jobs.kv.exitCode"), full.exit_code === null || full.exit_code === undefined ? t("common.dash") : full.exit_code],
      [t("jobs.col.created"), fmtTime(full.created)],
      [t("jobs.kv.started"), fmtTime(full.started)],
      [t("jobs.kv.finished"), fmtTime(full.finished)],
      [t("jobs.col.duration"), full.duration_human || t("common.dash")],
      [t("jobs.kv.cwd"), h("span", { class: "mono small" }, full.cwd || t("common.dash"))],
      [t("jobs.kv.lines"), full.lines],
      [t("jobs.kv.error"), full.error || t("common.dash")],
    ];
    if (full.meta && Object.keys(full.meta).length) {
      for (const [key, value] of Object.entries(full.meta)) rows.push([`meta.${key}`, String(value)]);
    }
    modal({
      title: t("jobs.detailTitle", { id: full.id }),
      width: 720,
      body: h("div", {}, kvRows(rows),
        (detail && detail.lines || []).length
          ? h("pre", { class: "console mono-block", style: { height: "240px", marginTop: "12px" } },
              (detail.lines || []).map((line) => line.text).join("\n"))
          : null),
      actions: [
        { label: t("jobs.liveLog"), kind: "primary", onClick: (close) => { close(); openLog(job); } },
        { label: t("common.buttons.close") },
      ],
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Старт                                                              */
  /* ------------------------------------------------------------------ */

  mount(root, head, filterBar, host);
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, t("jobs.title")),
      h("div", { class: "view-sub" }, t("jobs.subtitle"))),
    h("div", { class: "toolbar" },
      button(t("jobs.backHome"), () => (location.hash = "#/"), { kind: "ghost" }),
      button(t("nav.settings.title"), () => (location.hash = "#/settings"), { kind: "ghost" })));

  mount(host, tableHost);
  mount(tableHost, spinner());
  await load();

  cleanups.push(poller(5000, () => (document.hidden ? null : load()), { immediate: false }));
  return () => cleanups.forEach((stop) => stop && stop());
}
