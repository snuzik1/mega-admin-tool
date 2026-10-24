/* Изменения в системе: файлы, пакеты, входы, службы, ошибки в журналах
   и собственный журнал аудита MegaTool. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, card, chip, empty, fmtBytes, fmtNumber, fmtTime, guard, h, mount, notice,
  poller, spinner, table, timeAgo, toolbar,
} from "../ui.js";

/* Диапазоны времени: в key — ключ словаря (переводится при выводе). */
const RANGES = [
  { hours: 1, key: "changes.range.h1" },
  { hours: 6, key: "changes.range.h6" },
  { hours: 24, key: "changes.range.h24" },
  { hours: 72, key: "changes.range.d3" },
  { hours: 168, key: "changes.range.d7" },
];

const SECTION_ORDER = ["files", "packages", "logins", "services", "logs", "audit"];

export async function render(root) {
  const cleanups = [];
  let hours = 24;
  let data = null;
  let fileFilter = "";

  const head = h("div", { class: "view-head" });
  const summaryHost = h("div");
  const bodyHost = h("div");
  const rangeBar = h("div", { class: "chips" });

  const load = async () => {
    mount(bodyHost, spinner(t("changes.loading")));
    const result = await guard(() => api.get("/api/changes", { hours }), { quiet: true });
    data = result && result.data ? result.data : null;
    drawSummary();
    drawBody();
  };

  const setRange = (value) => {
    hours = value;
    drawRangeBar();
    load();
  };

  const drawRangeBar = () => {
    mount(rangeBar, RANGES.map((range) =>
      chip(t(range.key), { active: range.hours === hours, onclick: () => setRange(range.hours) })));
  };

  /* ------------------------------------------------------------------ */
  /*  Сводка                                                             */
  /* ------------------------------------------------------------------ */

  const drawSummary = () => {
    if (!data) {
      mount(summaryHost, notice(t("changes.loadFailed"), "bad"));
      return;
    }
    const sections = data.sections || [];
    const range = RANGES.find((item) => item.hours === hours);
    mount(summaryHost, card(t("changes.summary", { range: range ? t(range.key) : t("changes.hours", { n: hours }) }),
      h("div", {},
        h("div", { class: "grid cards-3" }, SECTION_ORDER.map((id) => {
          const section = sections.find((item) => item.id === id);
          if (!section) return null;
          const count = (section.items || []).length;
          return h("div", { class: "stat", style: { cursor: "pointer" }, onclick: () => scrollTo(id) },
            h("div", { class: "stat-label" }, section.title),
            h("div", { class: "stat-value" }, String(count)),
            h("div", { class: "stat-detail" }, section.available === false ? t("changes.sourceUnavailable") : section.hint || ""));
        })),
        h("p", { class: "small muted", style: { marginTop: "10px" } },
          t("changes.generated", { time: fmtTime(data.generated_at) })))
      , { extra: toolbar(button(t("common.buttons.refresh"), () => load(), { size: "sm", kind: "ghost" })) }));
  };

  const scrollTo = (id) => {
    const node = document.getElementById(`changes-${id}`);
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  /* ------------------------------------------------------------------ */
  /*  Разделы                                                            */
  /* ------------------------------------------------------------------ */

  const sectionHeader = (section) => {
    const count = (section.items || []).length;
    return h("div", { class: "row" },
      h("h3", {}, section.title),
      badge(String(count), section.available === false ? "muted" : count ? "neutral" : "ok"));
  };

  const wrap = (section, body, extra = null) =>
    h("section", { class: "card", id: `changes-${section.id}` },
      h("div", { class: "card-head" }, sectionHeader(section), extra),
      h("div", { class: "card-body" },
        section.available === false
          ? notice(section.hint || t("changes.sectionUnavailable"), "warn")
          : body));

  const drawFiles = (section) => {
    const items = (section.items || []).filter((item) =>
      !fileFilter || item.path.toLowerCase().includes(fileFilter.toLowerCase()));
    const search = h("input", {
      class: "input",
      placeholder: t("changes.files.filter"),
      style: { maxWidth: "300px" },
      value: fileFilter,
      oninput: (event) => {
        fileFilter = event.target.value;
        drawBody();
      },
    });
    const body = h("div", {},
      h("div", { class: "toolbar" }, search, h("span", { class: "small muted" }, t("changes.files.scanned", { n: fmtNumber(section.scanned_dirs || 0, 0) }))),
      table({
        columns: [
          { key: "time", label: t("changes.files.modified"), render: (row) => h("span", { class: "nowrap" }, timeAgo(row.time)) },
          { key: "path", label: t("changes.files.path"), render: (row) => h("span", { class: "mono small" }, row.path) },
          { key: "size", label: t("changes.col.size"), align: "right", render: (row) => fmtBytes(row.size) },
          { key: "root", label: t("changes.files.root"), render: (row) => h("span", { class: "small muted" }, row.root) },
        ],
        rows: items,
        empty: fileFilter ? t("changes.files.emptyFiltered") : t("changes.files.empty"),
      }));
    return wrap(section, body);
  };

  const simpleItems = (section, emptyText) =>
    wrap(section, table({
      columns: [
        { key: "time", label: t("changes.col.time"), render: (row) => row.time ? h("span", { class: "nowrap" }, String(row.time).length > 20 ? fmtTime(row.time) : row.time) : t("common.dash") },
        { key: "kind", label: t("changes.col.kind"), render: (row) => row.kind ? badge(row.kind, "muted") : t("common.dash") },
        { key: "title", label: t("changes.col.event"), render: (row) => h("span", {}, row.title) },
        { key: "detail", label: t("changes.col.detail"), render: (row) => h("span", { class: "small muted" }, row.detail || t("common.dash")) },
      ],
      rows: section.items || [],
      empty: emptyText,
    }));

  const drawBody = () => {
    if (!data) {
      mount(bodyHost, empty(t("changes.reportUnavailable")));
      return;
    }
    const sections = data.sections || [];
    const nodes = [];
    for (const id of SECTION_ORDER) {
      const section = sections.find((item) => item.id === id);
      if (!section) continue;
      if (id === "files") nodes.push(drawFiles(section));
      else if (id === "packages") nodes.push(simpleItems(section, t("changes.empty.packages")));
      else if (id === "logins") nodes.push(simpleItems(section, t("changes.empty.logins")));
      else if (id === "services") nodes.push(simpleItems(section, t("changes.empty.services")));
      else if (id === "logs") nodes.push(simpleItems(section, t("changes.empty.logs")));
      else nodes.push(simpleItems(section, t("changes.empty.audit")));
    }
    mount(bodyHost, nodes.length ? nodes : empty(t("changes.sectionsUnavailable")));
  };

  /* ------------------------------------------------------------------ */
  /*  Старт                                                              */
  /* ------------------------------------------------------------------ */

  mount(root, head, summaryHost, bodyHost);
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, t("changes.title")),
      h("div", { class: "view-sub" }, t("changes.subtitle"))),
    rangeBar);

  drawRangeBar();
  await load();

  cleanups.push(poller(60000, () => (document.hidden ? null : load()), { immediate: false }));
  return () => cleanups.forEach((stop) => stop && stop());
}
