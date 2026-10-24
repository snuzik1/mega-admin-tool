/* Серверы по SSH: инвентаризация, каталог задач, генерация скрипта и
   Ansible-плейбука, выполнение через менеджер задач с живым журналом. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, card, confirmDialog, empty, field, fmtNumber, fmtTime, guard, h,
  input, jobModal, kvRows, modal, mount, notice, promptDialog, select, spinner,
  table, toast, toolbar,
} from "../ui.js";

const RISK_KINDS = { safe: "ok", medium: "warn", high: "bad" };
const TOOL_KEYS = [
  ["docker", "servers.tools.docker"],
  ["compose", "servers.tools.compose"],
  ["podman", "servers.tools.podman"],
  ["nginx", "servers.tools.nginx"],
  ["ufw", "servers.tools.ufw"],
  ["firewall_cmd", "servers.tools.firewall"],
  ["systemctl", "servers.tools.systemctl"],
  ["fail2ban", "servers.tools.fail2ban"],
  ["curl", "servers.tools.curl"],
  ["git", "servers.tools.git"],
];

function createdText(seconds) {
  if (!seconds) return t("common.dash");
  const date = new Date(Number(seconds) * 1000);
  return isNaN(date.getTime()) ? t("common.dash") : fmtTime(date.toISOString());
}

async function copyText(text) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (error) {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.left = "-10000px";
      document.body.append(area);
      area.select();
      copied = document.execCommand("copy");
      area.remove();
    } catch (fallbackError) {
      copied = false;
    }
  }
  toast(copied ? t("common.copied") : t("common.copyFailed"), copied ? "success" : "error");
}

function downloadText(filename, text) {
  const blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = h("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function render(root) {
  let listing = { items: [], ssh: { available: true }, remote: true };
  let selectedId = null;
  let catalog = null;
  let selectedTasks = new Set();
  let paramValues = {};
  let generation = 0;

  const head = h("div", { class: "view-head" });
  const noticeHost = h("div");
  const listHost = h("div", { class: "col" });
  const detailHost = h("div", { class: "col" });

  const selectedServer = () => (listing.items || []).find((item) => item.id === selectedId) || null;
  const sshReady = () => Boolean(listing.ssh && listing.ssh.available);
  const remoteReady = () => listing.remote !== false;
  const canRun = () => remoteReady() && sshReady();
  const tasks = () => (catalog && catalog.items) || [];
  const selectedOrdered = () => tasks().filter((task) => selectedTasks.has(task.id));

  /* ------------------------------------------------------------------ */
  /*  Загрузка списка серверов                                           */
  /* ------------------------------------------------------------------ */

  const loadServers = async () => {
    const result = await guard(() => api.get("/api/servers"), { quiet: true });
    if (!result || result.ok === false) {
      mount(listHost, empty((result && result.error) || t("servers.toast.loadFailed"), { mark: "!" }));
      return false;
    }
    listing = {
      items: result.items || [],
      ssh: result.ssh || { available: true },
      remote: result.remote !== false,
    };
    if (selectedId && !listing.items.some((item) => item.id === selectedId)) {
      selectedId = null;
      catalog = null;
      selectedTasks = new Set();
      paramValues = {};
    }
    drawNotice();
    drawServers();
    return true;
  };

  const drawNotice = () => {
    const nodes = [];
    if (!sshReady()) {
      nodes.push(
        notice(t("servers.sshMissing"), "warn"),
        h("p", { class: "small muted" }, t("servers.sshMissingHint"))
      );
    }
    if (!remoteReady()) nodes.push(notice(t("servers.remoteDisabled"), "bad"));
    mount(noticeHost, nodes);
  };

  /* ------------------------------------------------------------------ */
  /*  Список серверов                                                    */
  /* ------------------------------------------------------------------ */

  const drawServers = () => {
    if (!listing.items.length) {
      mount(listHost, empty(t("servers.empty.title"), {
        mark: "🖥",
        hint: t("servers.empty.hint"),
        extra: toolbar(button(t("servers.add"), () => openEditor(), { kind: "primary", size: "sm" })),
      }));
      return;
    }

    const cards = listing.items.map((server) => {
      const report = server.last_check;
      const status = report
        ? report.ok
          ? badge(t("servers.card.reachable"), "ok", report.hostname || "")
          : badge(t("servers.card.unreachable"), "bad", report.error || "")
        : badge(t("servers.card.noCheck"), "muted");
      const tools = (report && report.ok && report.installed)
        ? Object.entries(report.installed).filter(([, value]) => value).map(([key]) =>
            badge({
              "base-tools": t("servers.tools.curl"),
              "docker-install": t("servers.tools.docker"),
              "web-nginx": t("servers.tools.nginx"),
              "security-fail2ban": t("servers.tools.fail2ban"),
              "security-fw": t("servers.tools.ufw"),
            }[key] || key, "neutral"))
        : [];

      const element = card(server.name, h("div", { class: "col" },
        h("div", { class: "mono small" }, `${server.user}@${server.host}:${server.port}`),
        h("div", { class: "row row-wrap" },
          status,
          server.id === selectedId ? badge(t("servers.card.selected"), "neutral") : null),
        server.note ? h("div", { class: "small muted" }, server.note) : null,
        tools.length ? h("div", { class: "chips" }, tools) : null
      ), {
        extra: toolbar(
          button("", () => checkServer(server), {
            icon: "⟳", size: "sm", kind: "ghost", title: t("servers.card.check"),
            disabled: !canRun(),
          }),
          button("", () => openEditor(server), {
            icon: "✎", size: "sm", kind: "ghost", title: t("servers.card.edit"),
          }),
          button("", () => deleteServer(server), {
            icon: "✕", size: "sm", kind: "ghost", title: t("servers.card.delete"),
          })
        ),
      });
      element.setAttribute("role", "button");
      element.tabIndex = 0;
      element.style.cursor = "pointer";
      element.addEventListener("click", (event) => {
        if (event.target.closest("button, a, input, select, textarea")) return;
        selectServer(server.id);
      });
      element.addEventListener("keydown", (event) => {
        if (event.target !== element) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectServer(server.id);
        }
      });
      return element;
    });
    mount(listHost, h("div", { class: "grid cards-3" }, cards));
  };

  /* ------------------------------------------------------------------ */
  /*  Детали выбранного сервера                                          */
  /* ------------------------------------------------------------------ */

  const drawDetail = () => {
    const server = selectedServer();
    if (!server) {
      mount(detailHost, empty(t("servers.detail.noSelection"), {
        mark: "⌁",
        hint: t("servers.detail.noSelectionHint"),
      }));
      return;
    }

    const report = server.last_check;
    const inventory = (() => {
      if (!report) {
        return h("div", {},
          h("p", { class: "muted" }, t("servers.inventory.empty")),
          h("p", { class: "small muted" }, t("servers.inventory.emptyHint")));
      }
      if (!report.ok) return notice(report.error || t("servers.card.unreachable"), "bad");

      const tools = TOOL_KEYS.map(([key, label]) => {
        const value = (report.tools || {})[key];
        return badge(t(label), value ? "ok" : "muted", value || t("common.dash"));
      });
      return h("div", { class: "col" },
        kvRows([
          [t("servers.kv.hostname"), report.hostname],
          [t("servers.kv.os"), report.os_pretty || report.os_id],
          [t("servers.kv.kernel"), [report.kernel, report.arch].filter(Boolean).join(" · ")],
          [t("servers.kv.sudo"), report.sudo_nopass
            ? badge(t("servers.inventory.sudoYes"), "ok")
            : badge(t("servers.inventory.sudoNo"), "warn")],
          [t("servers.kv.disk"), report.disk_avail],
          [t("servers.kv.mem"), report.mem_mb
            ? t("servers.inventory.memValue", { n: fmtNumber(report.mem_mb, 0) })
            : t("common.dash")],
          [t("servers.kv.loginUser"), report.user],
          [t("servers.kv.checked"), fmtTime(report.checked_at)],
        ]),
        h("h4", {}, t("servers.inventory.tools")),
        h("div", { class: "chips" }, tools)
      );
    })();

    const serverCard = card(t("servers.detail.title", { name: server.name }), h("div", {},
      kvRows([
        [t("servers.kv.host"), server.host],
        [t("servers.kv.port"), server.port],
        [t("servers.kv.user"), server.user],
        [t("servers.kv.key"), server.key || t("servers.kv.keyEmpty")],
        [t("servers.kv.note"), server.note],
        [t("servers.kv.created"), createdText(server.created_ts)],
      ])
    ), {
      extra: toolbar(
        button(t("servers.card.check"), () => checkServer(server), {
          kind: "primary", size: "sm", disabled: !canRun(),
        }),
        button(t("servers.detail.exec"), () => execCommand(server), {
          kind: "ghost", size: "sm", disabled: !canRun(),
        })
      ),
    });

    mount(detailHost,
      serverCard,
      card(t("servers.inventory.title"), inventory, {
        extra: toolbar(button(t("common.buttons.refresh"), () => checkServer(server), {
          size: "sm", kind: "ghost", disabled: !canRun(),
        })),
      }),
      drawTasksCard(server)
    );
  };

  /* ------------------------------------------------------------------ */
  /*  Каталог задач и выбор                                              */
  /* ------------------------------------------------------------------ */

  const drawTasksCard = (server) => {
    const tasksHost = h("div");
    const selectionInfo = h("span", { class: "small muted" });
    let runBtn = null;
    let scriptBtn = null;
    let playbookBtn = null;

    const sync = () => {
      const count = selectedTasks.size;
      selectionInfo.textContent = count
        ? t("servers.tasks.selected", { count })
        : t("servers.tasks.none");
      for (const control of [runBtn, scriptBtn, playbookBtn]) {
        if (control) control.disabled = count === 0 || (control === runBtn && !canRun());
      }
    };

    const drawTable = () => {
      if (!catalog) {
        mount(tasksHost, empty(t("servers.tasks.loadFailed")));
        return;
      }
      const groups = Object.fromEntries((catalog.groups || []).map((group) => [group.id, group.title]));
      const rows = tasks().map((task) => {
        const values = paramValues[task.id] || {};
        const summary = task.params
          .map((spec) => {
            const value = values[spec.name] !== undefined ? values[spec.name] : spec.default;
            return value ? `${spec.label}: ${value}` : null;
          })
          .filter(Boolean)
          .join(" · ");
        return Object.assign({}, task, { paramSummary: summary });
      });

      const widget = table({
        columns: [
          {
            key: "pick", label: t("servers.col.pick"), width: "52px", sortable: false,
            render: (row) => h("input", {
              type: "checkbox",
              checked: selectedTasks.has(row.id),
              onchange: (event) => {
                if (event.target.checked) selectedTasks.add(row.id);
                else selectedTasks.delete(row.id);
                sync();
              },
            }),
          },
          {
            key: "title", label: t("servers.col.task"),
            render: (row) => h("div", {},
              h("div", {}, row.title),
              h("div", { class: "small muted" }, row.description),
              row.paramSummary ? h("div", { class: "small mono muted" }, row.paramSummary) : null),
          },
          {
            key: "group", label: t("servers.col.group"),
            render: (row) => badge(groups[row.group] || row.group, "neutral"),
          },
          {
            key: "risk", label: t("servers.col.risk"), width: "130px",
            render: (row) => badge((catalog.risks || {})[row.risk] || row.risk, RISK_KINDS[row.risk] || "neutral"),
          },
          {
            key: "sudo", label: t("servers.col.sudo"), width: "70px",
            render: (row) => row.sudo ? badge(t("servers.tasks.sudo"), "warn") : t("common.dash"),
          },
          {
            key: "installed", label: t("servers.col.installed"), width: "110px",
            render: (row) => row.installed === true
              ? badge(t("servers.tasks.installed"), "ok")
              : row.installed === false
                ? badge(t("servers.tasks.notInstalled"), "muted")
                : t("common.dash"),
          },
          {
            key: "params", label: t("servers.col.params"), sortable: false, width: "130px",
            render: (row) => row.params.length
              ? button(t("servers.tasks.params"), () => openParams(row), { size: "sm", kind: "ghost" })
              : t("common.dash"),
          },
        ],
        rows,
        empty: t("servers.tasks.empty"),
      });
      mount(tasksHost, widget);
    };

    runBtn = button(t("servers.tasks.run"), () => runSelected(server), {
      kind: "primary", size: "sm", disabled: true,
    });
    scriptBtn = button(t("servers.tasks.script"), () => showScript(server), {
      kind: "ghost", size: "sm", disabled: true,
    });
    playbookBtn = button(t("servers.tasks.playbook"), () => showPlaybook(server), {
      kind: "ghost", size: "sm", disabled: true,
    });

    drawTable();
    sync();

    const selectAll = () => {
      for (const task of tasks()) selectedTasks.add(task.id);
      drawTable();
      sync();
    };
    const clearAll = () => {
      selectedTasks.clear();
      drawTable();
      sync();
    };

    return card(t("servers.tasks.title"), h("div", { class: "col" },
      h("div", { class: "toolbar" },
        button(t("servers.tasks.selectAll"), selectAll, { size: "sm", kind: "ghost" }),
        button(t("servers.tasks.clear"), clearAll, { size: "sm", kind: "ghost" }),
        h("span", { class: "spacer" }),
        selectionInfo
      ),
      tasksHost,
      !catalog ? null : h("div", { class: "toolbar" }, runBtn, scriptBtn, playbookBtn)
    ));
  };

  /* ------------------------------------------------------------------ */
  /*  Параметры задач                                                    */
  /* ------------------------------------------------------------------ */

  const openParams = (task) => {
    const current = paramValues[task.id] || {};
    const controls = {};
    const body = h("div", { class: "col" }, task.params.map((spec) => {
      const value = current[spec.name] !== undefined ? current[spec.name] : spec.default;
      let control;
      if (spec.kind === "choice") {
        control = select((spec.choices || []).map((choice) => ({
          value: choice,
          label: (spec.choice_labels || {})[choice] || choice,
        })), { value });
      } else if (spec.kind === "int") {
        control = input({
          type: "number",
          min: spec.min === null || spec.min === undefined ? "" : String(spec.min),
          max: spec.max === null || spec.max === undefined ? "" : String(spec.max),
          value: value === null || value === undefined ? "" : String(value),
        });
      } else {
        control = input({ value: value === null || value === undefined ? "" : String(value) });
      }
      controls[spec.name] = control;
      const hint = [
        spec.hint,
        spec.optional ? t("servers.params.optional") : "",
        spec.kind === "int" && spec.min !== null && spec.min !== undefined
          ? t("servers.params.range", { min: spec.min, max: spec.max })
          : "",
      ].filter(Boolean).join(" · ");
      return field(spec.label, control, hint);
    }));

    modal({
      title: t("servers.params.title", { task: task.title }),
      width: 560,
      body,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.save"),
          kind: "primary",
          onClick: (close) => {
            paramValues[task.id] = Object.fromEntries(
              task.params.map((spec) => [spec.name, controls[spec.name].value])
            );
            close();
            // Параметры показаны в строке задачи — обновляем карточку целиком.
            drawDetail();
          },
        },
      ],
    });
  };

  const collectParams = () => {
    const result = {};
    for (const task of selectedOrdered()) {
      const values = paramValues[task.id] || {};
      for (const spec of task.params) {
        const value = values[spec.name] !== undefined ? values[spec.name] : spec.default;
        const text = value === null || value === undefined ? "" : String(value);
        // Одинаковые имена (user) используют общее значение: первый
        // непустой вариант из порядка каталога применяется ко всем задачам.
        if (!(spec.name in result)) result[spec.name] = text;
        else if (!result[spec.name] && text) result[spec.name] = text;
      }
    }
    return result;
  };

  /* ------------------------------------------------------------------ */
  /*  Проверка и операции на сервере                                     */
  /* ------------------------------------------------------------------ */

  const checkServer = async (server) => {
    const result = await guard(() => api.post(`/api/servers/${server.id}/check`), { quiet: true });
    if (!result) return;
    if (!result.ok) {
      server.last_check = { ok: false, reachable: false, error: result.error || "" };
      toast(result.error || t("servers.toast.checkFailed"), "error", 8000);
    } else {
      server.last_check = result;
      toast(t("servers.toast.checkOk", { name: server.name }), "success");
    }
    drawServers();
    if (selectedId === server.id) await selectServer(server.id);
    else drawDetail();
  };

  const askRun = async (server) => {
    if (!selectedTasks.size) {
      toast(t("servers.toast.needSelection"), "warn");
      return;
    }
    const chosen = selectedOrdered();
    const high = chosen.filter((task) => task.risk === "high");
    let message = t("servers.confirm.run", { count: chosen.length, name: server.name });
    if (high.length) {
      message = t("servers.confirm.highRisk", {
        tasks: high.map((task) => task.title).join(", "),
      }) + "\n" + message;
    }
    const confirmed = await confirmDialog(message, {
      title: t("servers.confirm.runTitle"),
      okLabel: t("servers.buttons.run"),
    });
    if (!confirmed) return;
    const result = await submitRun(server, false);
    if (!result) return;
    toast(t("servers.toast.started"), "success");
    if (result.job) jobModal(result.job);
  };

  const submitRun = async (server, dryRun) => {
    const result = await guard(() => api.post(`/api/servers/${server.id}/run`, {
      tasks: selectedOrdered().map((task) => task.id),
      params: collectParams(),
      dry_run: Boolean(dryRun),
    }), { quiet: true });
    if (!result) return null;
    if (!result.ok) {
      toast(result.error || (dryRun ? t("servers.toast.scriptFailed") : t("servers.toast.runFailed")), "error", 8000);
      return null;
    }
    return result;
  };

  const showScript = async (server) => {
    const result = await submitRun(server, true);
    if (!result) return;
    const chosen = selectedOrdered();
    const high = chosen.filter((task) => task.risk === "high");
    modal({
      title: t("servers.script.title"),
      width: 940,
      body: h("div", { class: "col" },
        high.length ? notice(t("servers.script.highRisk", {
          tasks: high.map((task) => task.title).join(", "),
        }), "warn") : null,
        h("p", { class: "small muted" }, t("servers.script.hint", { name: server.name })),
        h("pre", { class: "console", style: { height: "420px" } }, result.script)
      ),
      actions: [
        { label: t("common.buttons.copy"), onClick: () => copyText(result.script) },
        {
          label: t("servers.script.execute"),
          kind: "primary",
          disabled: !canRun(),
          onClick: async (close) => {
            const confirmed = await confirmDialog(
              t("servers.script.runConfirm", { name: server.name }),
              { title: t("servers.confirm.runTitle"), okLabel: t("servers.buttons.run") }
            );
            if (!confirmed) return;
            const started = await submitRun(server, false);
            if (!started) return;
            close();
            toast(t("servers.toast.started"), "success");
            if (started.job) jobModal(started.job);
          },
        },
        { label: t("common.buttons.close") },
      ],
    });
  };

  const showPlaybook = async (server) => {
    const result = await guard(() => api.post(`/api/servers/${server.id}/playbook`, {
      tasks: selectedOrdered().map((task) => task.id),
      params: collectParams(),
    }), { quiet: true });
    if (!result) return;
    if (!result.ok) {
      toast(result.error || t("servers.toast.playbookFailed"), "error", 8000);
      return;
    }
    modal({
      title: t("servers.playbook.title"),
      width: 940,
      body: h("div", { class: "col" },
        h("p", { class: "small muted" }, t("servers.playbook.hint")),
        h("pre", { class: "console", style: { height: "440px" } }, result.yaml)
      ),
      actions: [
        { label: t("common.buttons.copy"), onClick: () => copyText(result.yaml) },
        { label: t("common.buttons.download"), onClick: () => downloadText(result.filename, result.yaml) },
        { label: t("common.buttons.close") },
      ],
    });
  };

  const execCommand = async (server) => {
    const command = await promptDialog(
      t("servers.exec.prompt", { name: server.name, user: server.user }),
      { title: t("servers.exec.title"), placeholder: t("servers.exec.placeholder") }
    );
    if (!command || !command.trim()) return;
    const confirmed = await confirmDialog(
      t("servers.exec.confirm", { name: server.name, command: command.trim() }),
      { title: t("servers.exec.title"), okLabel: t("servers.exec.run") }
    );
    if (!confirmed) return;
    const result = await guard(() => api.post(`/api/servers/${server.id}/exec`, {
      command: command.trim(),
    }), { quiet: true });
    if (!result) return;
    if (!result.ok) {
      toast(result.error || t("servers.toast.execFailed"), "error", 8000);
      return;
    }
    toast(t("servers.toast.started"), "success");
    if (result.job) jobModal(result.job);
  };

  const deleteServer = async (server) => {
    const confirmed = await confirmDialog(
      t("servers.confirm.delete", { name: server.name }),
      { title: t("servers.card.delete"), okLabel: t("common.buttons.delete") }
    );
    if (!confirmed) return;
    const result = await guard(() => api.del(`/api/servers/${server.id}`), { quiet: true });
    if (!result) return;
    if (!result.ok) {
      toast(result.error || t("servers.toast.loadFailed"), "error");
      return;
    }
    toast(t("servers.toast.deleted"), "success");
    await loadServers();
    if (listing.items.length) await selectServer(listing.items[0].id);
    else drawDetail();
  };

  /* ------------------------------------------------------------------ */
  /*  Форма добавления и изменения                                       */
  /* ------------------------------------------------------------------ */

  const openEditor = (server = null) => {
    const name = input({ value: server ? server.name : "", placeholder: t("servers.form.namePlaceholder") });
    const host = input({ value: server ? server.host : "", placeholder: t("servers.form.hostPlaceholder") });
    const port = input({
      type: "number", min: "1", max: "65535",
      value: String(server ? server.port : 22),
    });
    const user = input({ value: server ? server.user : "root", placeholder: "root" });
    const key = input({ value: server ? server.key : "", placeholder: t("servers.form.keyPlaceholder") });
    const note = input({ value: server ? server.note : "", placeholder: t("servers.form.notePlaceholder") });

    modal({
      title: server ? t("servers.form.editTitle") : t("servers.form.addTitle"),
      width: 620,
      body: h("div", { class: "col" },
        field(t("servers.form.name"), name),
        h("div", { class: "grid cards-2" },
          field(t("servers.form.host"), host),
          field(t("servers.form.port"), port)
        ),
        h("div", { class: "grid cards-2" },
          field(t("servers.form.user"), user),
          field(t("servers.form.key"), key)
        ),
        field(t("servers.form.note"), note)
      ),
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.save"),
          kind: "primary",
          onClick: async (close) => {
            if (!host.value.trim()) {
              toast(t("servers.form.hostRequired"), "warn");
              return;
            }
            const portNumber = Number(port.value);
            if (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
              toast(t("servers.form.portInvalid"), "warn");
              return;
            }
            const payload = {
              name: name.value.trim(),
              host: host.value.trim(),
              port: portNumber,
              user: user.value.trim() || "root",
              key: key.value.trim(),
              note: note.value.trim(),
            };
            const result = await guard(
              () => server
                ? api.patch(`/api/servers/${server.id}`, payload)
                : api.post("/api/servers", payload),
              { quiet: true }
            );
            if (!result) return;
            if (!result.ok) {
              toast(result.error || t("servers.form.saveFailed"), "error", 8000);
              return;
            }
            close();
            toast(t("servers.toast.saved"), "success");
            const saved = result.server || {};
            await loadServers();
            if (saved.id) await selectServer(saved.id);
            else if (listing.items.length) await selectServer(listing.items[0].id);
          },
        },
      ],
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Выбор сервера и загрузка каталога                                  */
  /* ------------------------------------------------------------------ */

  const selectServer = async (serverId) => {
    if (!serverId) {
      selectedId = null;
      catalog = null;
      drawServers();
      drawDetail();
      return;
    }
    if (selectedId !== serverId) {
      // Другой сервер — выбор задач и параметры сбрасываются.
      selectedTasks = new Set();
      paramValues = {};
    }
    selectedId = serverId;
    drawServers();
    const current = ++generation;
    mount(detailHost, spinner());
    const result = await guard(
      () => api.get("/api/servers/tasks", { sid: serverId }),
      { quiet: true }
    );
    if (current !== generation) return;
    if (!result || result.ok === false) {
      catalog = null;
      mount(detailHost, empty((result && result.error) || t("servers.tasks.loadFailed"), {
        mark: "!",
        extra: toolbar(button(t("common.buttons.retry"), () => selectServer(serverId), {
          size: "sm", kind: "ghost",
        })),
      }));
      return;
    }
    catalog = result;
    for (const task of tasks()) {
      if (!paramValues[task.id]) {
        paramValues[task.id] = Object.fromEntries(
          task.params.map((spec) => [spec.name, spec.default])
        );
      }
    }
    drawDetail();
  };

  /* ------------------------------------------------------------------ */
  /*  Шапка и запуск                                                     */
  /* ------------------------------------------------------------------ */

  const refreshAll = async () => {
    const keep = selectedId;
    await loadServers();
    if (keep && listing.items.some((item) => item.id === keep)) {
      await selectServer(keep);
    } else if (listing.items.length) {
      await selectServer(listing.items[0].id);
    } else {
      drawDetail();
    }
  };

  mount(root,
    head,
    noticeHost,
    h("div", { class: "grid cards-2" }, listHost, detailHost)
  );
  mount(head,
    h("div", {},
      h("h1", { class: "view-title" }, t("servers.title")),
      h("div", { class: "view-sub" }, t("servers.subtitle"))),
    toolbar(
      button(t("servers.add"), () => openEditor(), { kind: "primary" }),
      button(t("common.buttons.refresh"), refreshAll, { kind: "ghost" })
    )
  );

  await loadServers();
  if (listing.items.length) await selectServer(listing.items[0].id);
  else drawDetail();
}
