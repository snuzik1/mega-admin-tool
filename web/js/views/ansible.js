/* Ansible: инвентарь, плейбуки, запуск с живым логом, ad-hoc команды. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, card, empty, field, guard, h, input, jobModal, kvRows, modal, mount, notice,
  poller, select, stateBadge, table, tabs, toast, toolbar,
} from "../ui.js";

const INSTALL_HINT = "pip install ansible   •   sudo apt install ansible   •   winget install RedHat.Ansible";

export async function render(root) {
  const cleanups = [];
  let status = null;
  let inventory = null;
  let playbooks = [];
  let activeTab = "playbooks";

  const loadStatus = async (refresh = false) => {
    const result = await guard(() => api.get("/api/ansible/status", { refresh: refresh || undefined }), { quiet: true });
    status = (result && result.data) || null;
    return status;
  };

  const loadInventory = async () => {
    const result = await guard(() => api.get("/api/ansible/inventory"), { quiet: true });
    if (result && result.data) inventory = result.data;
    return inventory;
  };

  const loadPlaybooks = async () => {
    const result = await guard(() => api.get("/api/ansible/playbooks"), { quiet: true });
    playbooks = (result && result.items) || [];
    return playbooks;
  };

  await loadStatus();
  await loadInventory();

  const head = h("div", { class: "view-head" });
  const statusHost = h("div");
  const tabHost = h("div");
  const bodyHost = h("div");

  const drawStatus = () => {
    mount(statusHost, card(t("ansible.statusCard"), h("div", {},
      status && status.available
        ? h("div", {},
            kvRows([
              [t("ansible.version"), (status.version || "").split("\n")[0] || t("common.dash")],
              ["ansible-playbook", status.playbook_bin || t("ansible.notFound")],
              ["ansible", status.ansible_bin || t("ansible.notFound")],
              ["ansible-inventory", status.inventory_bin || t("ansible.notFound")],
              [t("ansible.inventory"), status.inventory],
              [t("ansible.playbooks"), status.playbooks_dir],
            ]))
        : h("div", {},
            notice(status ? status.error || t("ansible.notFoundTitle") : t("ansible.statusFailed"), "bad"),
            h("p", { class: "small muted" }, t("ansible.install", { hint: INSTALL_HINT })),
            h("p", { class: "small muted" }, t("ansible.binHint")))),
    { extra: toolbar(button(t("ansible.recheck"), async () => { await loadStatus(true); drawStatus(); }, { size: "sm", kind: "ghost" })) }));
  };

  /* ------------------------------------------------------------------ */
  /*  Инвентарь                                                          */
  /* ------------------------------------------------------------------ */

  const inventoryHost = h("div");

  const drawInventory = () => {
    if (!inventory) {
      mount(inventoryHost, empty(t("ansible.inventoryUnavailable"), { mark: "◆" }));
      return;
    }
    const tree = inventory.groups !== undefined ? inventory : inventory.tree || {};
    const editor = h("textarea", { class: "code-editor", style: { minHeight: "320px" }, spellcheck: "false", value: inventory.content || "" });
    const pathLabel = h("div", { class: "mono small muted" }, inventory.path || "");
    const groupsHost = h("div");

    const drawGroups = (data) => {
      mount(groupsHost, (data.groups || []).length
        ? table({
            columns: [
              { key: "name", label: t("ansible.group"), render: (row) => h("span", { class: "mono" }, row.name) },
              { key: "hosts", label: t("ansible.hosts"), render: (row) => (row.hosts || []).map((host) => badge(typeof host === "string" ? host : host.name, "muted")) },
              { key: "children", label: t("ansible.children"), render: (row) => (row.children || []).join(", ") || t("common.dash") },
              { key: "vars", label: t("ansible.vars"), render: (row) => h("span", { class: "mono small muted" }, Object.keys(row.vars || {}).join(", ") || t("common.dash")) },
            ],
            rows: data.groups || [],
            empty: t("ansible.groupsEmpty"),
          })
        : notice(data.error || t("ansible.inventoryParseFailed"), "warn"));
    };

    const save = async () => {
      const result = await guard(() => api.post("/api/ansible/inventory", { content: editor.value }), { quiet: true });
      if (!result) return;
      if (result.ok === false) {
        toast(result.error || t("ansible.inventorySaveFailed"), "error");
        return;
      }
      toast(t("ansible.inventorySaved"), "success");
      await loadInventory();
      drawGroups(result.data && result.data.tree ? result.data.tree : inventory);
      pathLabel.textContent = inventory.path || "";
    };

    mount(inventoryHost, h("div", {},
      h("div", { class: "toolbar", style: { marginBottom: "8px" } },
        pathLabel, badge(inventory.source || t("ansible.sourceFile"), "muted"),
        badge(t("ansible.hostsCount", { n: inventory.count ?? (inventory.hosts || []).length }), "neutral"),
        h("span", { class: "spacer" }),
        button(t("common.buttons.save"), save, { kind: "primary", size: "sm" }),
        button(t("common.buttons.refresh"), async () => { await loadInventory(); drawInventory(); }, { size: "sm", kind: "ghost" }),
        button(t("ansible.runPing"), () => adhocDialog("all", "ping"), { size: "sm", kind: "ghost" })),
      h("div", { class: "split" },
        h("div", { class: "col" }, editor),
        h("div", { class: "col" }, h("div", { class: "small muted" }, t("ansible.parsedGroups")), groupsHost))));
    drawGroups(tree);
  };

  /* ------------------------------------------------------------------ */
  /*  Плейбуки и запуск                                                  */
  /* ------------------------------------------------------------------ */

  const playbooksHost = h("div");

  const runDialog = (playbook) => {
    const limitInput = input({ placeholder: "web:&prod,!db1" });
    const tagsInput = input({ placeholder: "deploy,verify" });
    const skipTagsInput = input({ placeholder: "slow" });
    const checkInput = h("input", { type: "checkbox" });
    const diffInput = h("input", { type: "checkbox" });
    const verbositySelect = select([0, 1, 2, 3, 4].map((level) => ({ value: level, label: `-${"v".repeat(level) || t("ansible.none")}` })), { value: 0 });
    const varsInput = h("textarea", { class: "input", placeholder: "app_version=1.2.3\nenv=prod", rows: 3 });
    const inventoryInput = input({ value: (status && status.inventory) || "" });

    modal({
      title: t("ansible.runTitle", { name: playbook.name }),
      body: h("div", {},
        h("div", { class: "split" },
          h("div", {},
            field("--limit", limitInput, t("ansible.limitHint")),
            field("--tags", tagsInput),
            field("--skip-tags", skipTagsInput),
            field(t("ansible.extraVars"), varsInput, t("ansible.extraVarsHint"))),
          h("div", {},
            field(t("ansible.inventory"), inventoryInput),
            field(t("ansible.verbosity"), verbositySelect),
            h("label", { class: "check" }, checkInput, t("ansible.checkMode")),
            h("div", {}, h("label", { class: "check" }, diffInput, t("ansible.showDiff"))),
            h("p", { class: "small muted" }, (playbook.hosts || []).length ? t("ansible.playbookHosts", { hosts: playbook.hosts.join(", ") }) : ""),
            h("p", { class: "small muted" }, t("ansible.playbookStats", { tasks: playbook.task_count ?? "?", roles: (playbook.roles || []).join(", ") || t("ansible.none") }))))),
      width: 720,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.start"), kind: "primary",
          onClick: async (close) => {
            const extraVars = {};
            for (const line of varsInput.value.split("\n")) {
              const trimmed = line.trim();
              if (!trimmed || trimmed.startsWith("#")) continue;
              const index = trimmed.indexOf("=");
              if (index < 0) {
                toast(t("ansible.badVarLine", { line: trimmed }), "error");
                return;
              }
              extraVars[trimmed.slice(0, index).trim()] = trimmed.slice(index + 1);
            }
            const result = await guard(() => api.post("/api/ansible/run", {
              playbook: playbook.name,
              limit: limitInput.value.trim(),
              tags: tagsInput.value.trim(),
              skip_tags: skipTagsInput.value.trim(),
              check: checkInput.checked,
              diff: diffInput.checked,
              verbosity: Number(verbositySelect.value || 0),
              extra_vars: extraVars,
              inventory: inventoryInput.value.trim(),
            }), { quiet: true });
            if (!result) return;
            if (result.ok === false) {
              toast(result.error || t("ansible.runFailed"), "error", 7000);
              return;
            }
            close();
            const job = result.job || (result.data && result.data.job);
            if (job) jobModal(job, { title: `ansible-playbook ${playbook.name}` });
            loadJobs();
          },
        },
      ],
    });
  };

  const adhocDialog = (pattern = "all", module = "ping") => {
    const patternInput = input({ value: pattern });
    const moduleSelect = select(["ping", "command", "shell", "setup", "apt", "yum", "service", "file", "copy"].map((name) => ({ value: name, label: name })), { value: module });
    const argsInput = input({ placeholder: t("ansible.adhocArgs") });
    const inventoryInput = input({ value: (status && status.inventory) || "" });
    modal({
      title: t("ansible.adhocTitle"),
      body: h("div", {},
        field(t("ansible.pattern"), patternInput, t("ansible.patternHint")),
        field(t("ansible.module"), moduleSelect),
        field(t("ansible.args"), argsInput),
        field(t("ansible.inventory"), inventoryInput)),
      width: 560,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.start"), kind: "primary",
          onClick: async (close) => {
            const result = await guard(() => api.post("/api/ansible/ping", {
              pattern: patternInput.value.trim() || "all",
              module: moduleSelect.value,
              args: argsInput.value.trim(),
              inventory: inventoryInput.value.trim(),
            }), { quiet: true });
            if (!result) return;
            if (result.ok === false) {
              toast(result.error || t("ansible.runFailed"), "error");
              return;
            }
            close();
            const job = result.job || (result.data && result.data.job);
            if (job) jobModal(job, { title: `ansible ${moduleSelect.value}` });
            loadJobs();
          },
        },
      ],
    });
  };

  const syntaxCheck = async (playbook) => {
    const result = await guard(() => api.post("/api/ansible/check", { playbook: playbook.name }), { quiet: true });
    if (!result) return;
    modal({
      title: t("ansible.syntaxTitle", { name: playbook.name }),
      body: h("div", {},
        badge(result.ok ? t("ansible.syntaxOk") : t("ansible.syntaxErrors"), result.ok ? "ok" : "bad"),
        h("pre", { class: "mono-block", style: { minHeight: "160px" } }, (result.out || "") + (result.err || "") + (result.error || "") || t("ansible.empty"))),
      width: 780,
      actions: [{ label: t("common.buttons.close") }],
    });
  };

  const drawPlaybooks = async () => {
    await loadPlaybooks();
    mount(playbooksHost, playbooks.length
      ? h("div", { class: "grid cards-2" }, playbooks.map((playbook) =>
          card(playbook.name, h("div", { class: "col" },
            h("div", { class: "row row-wrap" },
              badge(t("ansible.tasksCount", { n: playbook.task_count ?? 0 }), "neutral"),
              (playbook.roles || []).length ? badge(t("ansible.rolesBadge", { roles: (playbook.roles || []).join(", ") }), "muted") : null,
              (playbook.hosts || []).length ? badge(t("ansible.hostsBadge", { hosts: (playbook.hosts || []).join(", ") }), "muted") : null,
              playbook.parse_error ? badge(t("ansible.yamlParseError"), "bad") : null),
            h("div", { class: "mono small muted" }, playbook.rel || playbook.path),
            h("div", { class: "small muted" }, t("ansible.modifiedAt", { date: (playbook.modified || "").replace("T", " ").slice(0, 19), size: playbook.size })),
            (playbook.plays || []).length
              ? table({
                  columns: [
                    { key: "name", label: t("ansible.play") },
                    { key: "hosts", label: t("ansible.hosts") },
                    { key: "tasks", label: t("ansible.tasks"), align: "right" },
                    { key: "become", label: "sudo", render: (row) => row.become ? t("ansible.yes") : t("common.dash") },
                  ],
                  rows: playbook.plays,
                })
              : null,
            h("div", { class: "toolbar", style: { marginTop: "8px" } },
              button(t("common.buttons.start"), () => runDialog(playbook), { kind: "primary", size: "sm" }),
              button(t("ansible.checkSyntax"), () => syntaxCheck(playbook), { size: "sm", kind: "ghost" }),
              button(t("ansible.editor"), () => { sessionStorage.setItem("megatool.openPath", playbook.path); location.hash = "#/configs"; }, { size: "sm", kind: "ghost" })),
            playbook.parse_error ? notice(playbook.parse_error, "bad") : null), { class: "card" })))
      : empty(t("ansible.noPlaybooks"), { mark: "◆", hint: t("ansible.catalogHint", { path: (status && status.playbooks_dir) || "" }) }));
  };

  /* ------------------------------------------------------------------ */
  /*  Запуски                                                            */
  /* ------------------------------------------------------------------ */

  const jobsHost = h("div");
  const loadJobs = async () => {
    const result = await guard(() => api.get("/api/jobs", { kind: "ansible", limit: 25 }), { quiet: true });
    if (!result) return;
    const items = result.items || [];
    mount(jobsHost, items.length
      ? table({
          columns: [
            { key: "title", label: t("ansible.job") },
            { key: "status", label: t("ansible.status"), render: (row) => stateBadge(row.status) },
            { key: "created", label: t("ansible.created"), render: (row) => h("span", { class: "mono small" }, (row.created || "").replace("T", " ").slice(0, 19)) },
            { key: "duration_human", label: t("ansible.duration") },
            { key: "exit_code", label: t("ansible.exitCode"), align: "right" },
            {
              key: "actions", label: "", render: (row) => h("div", { class: "row", style: { gap: "4px" } },
                button(t("ansible.openLog"), () => jobModal(row), { size: "xs", kind: "ghost" }),
                row.status === "running" ? button(t("common.buttons.stop"), async () => { await guard(() => api.post(`/api/jobs/${row.id}/cancel`), { success: t("common.console.cancelRequested") }); loadJobs(); }, { size: "xs", kind: "danger" }) : null),
            },
          ],
          rows: items,
          empty: t("ansible.jobsEmpty"),
        })
      : empty(t("ansible.noRuns"), { mark: "▶", hint: t("ansible.noRunsHint") }));
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладки                                                            */
  /* ------------------------------------------------------------------ */

  const drawTab = () => {
    switch (activeTab) {
      case "playbooks": mount(tabHost, toolbar(h("span", { class: "spacer" }), button(t("common.buttons.refresh"), drawPlaybooks, { kind: "ghost", size: "sm" }))); drawPlaybooks(); break;
      case "inventory": mount(tabHost, toolbar(h("span", { class: "spacer" }), button(t("ansible.editInventory"), () => { sessionStorage.setItem("megatool.openPath", (status && status.playbooks_dir) || ""); location.hash = "#/configs"; }, { kind: "ghost", size: "sm" }))); drawInventory(); break;
      case "jobs": mount(tabHost, toolbar(h("span", { class: "spacer" }), button(t("common.buttons.refresh"), loadJobs, { kind: "ghost", size: "sm" }))); loadJobs(); break;
    }
  };

  const tabBar = tabs(
    [
      { id: "playbooks", label: t("ansible.playbooks") },
      { id: "inventory", label: t("ansible.inventory") },
      { id: "jobs", label: t("ansible.runs") },
    ],
    { onChange: (id) => { activeTab = id; drawTab(); } }
  );

  mount(root, head, statusHost, h("div", { class: "col" }, tabBar, tabHost, bodyHost));
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, "Ansible"),
      h("div", { class: "view-sub" }, t("ansible.subtitle"))),
    h("div", { class: "toolbar" },
      button("Ad-hoc ping", () => adhocDialog("all", "ping"), { kind: "ghost" }),
      button(t("nav.settings.title"), () => (location.hash = "#/settings"), { kind: "ghost" })));

  mount(bodyHost, tabHost);
  drawStatus();
  drawTab();

  cleanups.push(poller(10000, () => {
    if (document.hidden || activeTab !== "jobs") return;
    return loadJobs();
  }, { immediate: false }));
  return () => cleanups.forEach((stop) => stop && stop());
}
