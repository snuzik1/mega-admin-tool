/* Настройки MegaTool: сервер, мониторинг и пороги, движки контейнеров,
   безопасность, интерфейс, данные и каталоги. */

import { api } from "../api.js";
import { setLang, t } from "../i18n.js";
import {
  badge, button, card, confirmDialog, empty, field, fmtBytes, fmtNumber,
  guard, h, input, kvRows, modal, mount, notice, select, spinner,
  table, tabs, toast, toolbar,
} from "../ui.js";

/* Списки выбора и подсказки храним с ключами словаря: подписи подставляются
   на этапе отрисовки, чтобы смена языка не оставляла устаревшие строки. */
const CONTAINER_ENGINES = [
  { value: "auto", key: "settings.engines.auto" },
  { value: "docker" },
  { value: "podman" },
];

const ENGINES = [
  ["docker", "docker", "settings.engines.hint.docker"],
  ["podman", "podman", "settings.engines.hint.podman"],
  ["kubectl", "kubectl", "settings.engines.hint.kubectl"],
  ["nginx", "nginx", "settings.engines.hint.nginx"],
  ["ansible", "ansible", "settings.engines.hint.ansible"],
  ["ansible_playbook", "ansible-playbook", "settings.engines.hint.ansiblePlaybook"],
  ["ansible_inventory", "ansible-inventory", "settings.engines.hint.ansibleInventory"],
  ["ssh", "ssh", "settings.engines.hint.ssh"],
  ["scp", "scp", "settings.engines.hint.scp"],
];

const THRESHOLDS = [
  ["cpu_pct", "settings.thresholds.cpu", 90],
  ["mem_pct", "settings.thresholds.mem", 90],
  ["swap_pct", "settings.thresholds.swap", 60],
  ["disk_pct", "settings.thresholds.disk", 90],
  ["load_per_core", "settings.thresholds.load", 2],
  ["temp_c", "settings.thresholds.temp", 85],
  ["sustain_sec", "settings.thresholds.sustain", 20],
];

const SAFETY = [
  ["allow_process_kill", "settings.safety.kill", "settings.safety.killHint"],
  ["allow_container_actions", "settings.safety.containers", "settings.safety.containersHint"],
  ["allow_k8s_delete", "settings.safety.k8sDelete", "settings.safety.k8sDeleteHint"],
  ["allow_file_delete", "settings.safety.fileDelete", "settings.safety.fileDeleteHint"],
];

const isLoopbackHost = (host) => {
  const value = String(host || "").trim().replace(/^\[|\]$/g, "").toLowerCase();
  return value === "localhost" || value === "::1" || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(value);
};

export async function render(root, { app = null, args = [] } = {}) {
  const cleanups = [];
  let data = null;       // config
  let tlsInfo = {};
  let paths = {};
  let workspace = {};
  let activeTab = ["server", "monitor", "agents", "engines", "safety", "system", "ui", "data"].includes(args[0]) ? args[0] : "server";
  let engineReport = null;

  const head = h("div", { class: "view-head" });
  const tabHost = h("div");
  const bodyHost = h("div");

  const load = async () => {
    const result = await guard(() => api.get("/api/settings"), { quiet: true });
    if (!result) return false;
    data = result.data || {};
    tlsInfo = result.tls || {};
    paths = result.paths || {};
    workspace = result.workspace || {};
    // Каталоги приложений/конфигов/запусков отдаёт отдельный обработчик
    const extra = await guard(() => api.get("/api/paths"), { quiet: true });
    if (extra && extra.data) paths = Object.assign({}, paths, extra.data);
    return true;
  };

  const save = async (patch, { restart = false, success = t("settings.saved") } = {}) => {
    const result = await guard(() => api.patch("/api/settings", patch), { success: "" });
    if (!result) return false;
    data = result.data || data;
    toast(success, "success");
    if (restart || result.restart_required) {
      modal({
        title: t("settings.restart.title"),
        width: 460,
        body: h("p", {}, t("settings.restart.body")),
        actions: [{ label: t("settings.restart.ok") }],
      });
    }
    return true;
  };

  /* ------------------------------------------------------------------ */
  /*  Общие помощники                                                    */
  /* ------------------------------------------------------------------ */

  const numberField = (label, value, { min = 0, max = null, step = 1, hint = "", onSave }) => {
    const control = input({ type: "number", value: String(value ?? ""), min: String(min), max: max === null ? undefined : String(max), step: String(step) });
    const submit = () => {
      let number = Number(control.value);
      if (!isFinite(number)) {
        toast(t("settings.number"), "warn");
        return;
      }
      if (number < min) number = min;
      if (max !== null && number > max) number = max;
      control.value = String(number);
      onSave(number);
    };
    const row = h("div", { class: "toolbar" }, control, button(t("common.buttons.save"), submit, { size: "sm", kind: "ghost" }));
    return field(label, row, hint);
  };

  const textField = (label, value, hint, onSave, { placeholder = "" } = {}) => {
    const control = input({ value: value || "", placeholder });
    const row = h("div", { class: "toolbar" }, control, button(t("common.buttons.save"), () => onSave(control.value.trim()), { size: "sm", kind: "ghost" }));
    return field(label, row, hint);
  };

  const linesField = (label, values, hint, onSave) => {
    const control = h("textarea", { class: "code-editor", style: { minHeight: "110px" }, spellcheck: "false", value: (values || []).join("\n") });
    const row = h("div", { class: "toolbar" }, button(t("settings.saveList"), () => {
      const list = control.value.split("\n").map((line) => line.trim()).filter(Boolean);
      onSave(list);
    }, { size: "sm", kind: "ghost" }));
    return field(label, h("div", { class: "col" }, control, row), hint);
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: сервер                                                    */
  /* ------------------------------------------------------------------ */

  const drawServer = (host) => {
    const server = data.server || {};
    const tls = server.tls || {};
    const info = tlsInfo || {};
    const external = !isLoopbackHost(server.host);
    const refresh = async () => {
      if (await load()) draw();
    };
    const generate = async () => {
      const ok = await confirmDialog(t("settings.server.tls.confirm"), {
        title: t("settings.server.tls.card"),
        okLabel: t("settings.server.tls.generate"),
        danger: false,
      });
      if (!ok) return;
      const result = await guard(() => api.post("/api/settings/tls/generate", { force: true }), { success: "" });
      if (!result) return;
      toast(t("settings.server.tls.generated"), "success");
      await refresh();
      modal({
        title: t("settings.restart.title"),
        width: 460,
        body: h("p", {}, t("settings.server.tls.restart")),
        actions: [{ label: t("settings.restart.ok") }],
      });
    };
    const enable = async () => {
      if (await save({ server: { tls: { enabled: true } } }, { success: t("settings.settingSaved") })) await refresh();
    };
    const disable = async () => {
      if (await save({ server: { tls: { enabled: false } } }, { success: t("settings.settingSaved") })) await refresh();
    };
    const status = info.error
      ? badge(t("settings.server.tls.error"), "bad")
      : tls.enabled
        ? badge(t("settings.server.tls.statusOn"), "ok")
        : badge(info.exists ? t("settings.server.tls.statusOff") : t("settings.server.tls.statusMissing"), external ? "bad" : "muted");
    const certControl = input({ value: tls.certfile || "", placeholder: info.certfile || "" });
    const keyControl = input({ value: tls.keyfile || "", placeholder: info.keyfile || "" });
    const savePaths = async () => {
      const saved = await save({
        server: {
          tls: {
            certfile: certControl.value.trim(),
            keyfile: keyControl.value.trim(),
          },
        },
      }, { restart: true, success: t("settings.server.tls.pathsSaved") });
      if (saved) await refresh();
    };
    mount(host,
      card(t("settings.server.card"), h("div", { class: "col" },
        notice(t("settings.server.notice"), "warn"),
        h("div", { class: "grid cards-2" },
          textField(t("settings.server.host"), server.host, t("settings.server.hostHint"), async (value) => {
            await save({ server: { host: value } }, { restart: true, success: t("settings.server.hostSaved") });
          }),
          numberField(t("settings.server.port"), server.port, {
            min: 1, max: 65535, hint: t("settings.server.portHint"),
            onSave: async (value) => { await save({ server: { port: value } }, { restart: true, success: t("settings.server.portSaved") }); },
          })),
        h("label", { class: "check" },
          h("input", {
            type: "checkbox",
            checked: Boolean(server.open_browser),
            onchange: (event) => save({ server: { open_browser: event.target.checked } }, { success: t("settings.settingSaved") }),
          }),
          h("span", {}, t("settings.server.openBrowser"))),
        linesField(t("settings.server.allowedHosts"), server.allowed_hosts || [], t("settings.server.allowedHostsHint"),
          async (list) => { await save({ server: { allowed_hosts: list } }, { success: t("settings.server.allowedHostsSaved") }); })
      )),

      card(t("settings.server.tls.card"), h("div", { class: "col" },
        h("div", { class: "toolbar" }, status),
        external && !tls.enabled ? notice(t("settings.server.tls.externalWarning"), "bad") : null,
        info.error ? notice(`${t("settings.server.tls.error")}: ${info.error}`, "bad") : null,
        h("p", { class: "small muted" }, t("settings.server.tls.desc")),
        h("div", { class: "grid cards-2" },
          field(t("settings.server.tls.certPath"), certControl),
          field(t("settings.server.tls.keyPath"), keyControl)),
        h("div", { class: "toolbar" },
          button(t("common.buttons.save"), savePaths, { size: "sm", kind: "ghost" })),
        h("p", { class: "small muted" }, t("settings.server.tls.pathHint")),
        kvRows([
          [t("settings.server.tls.cert"), h("span", { class: "mono small" }, info.certfile || t("common.dash"))],
          [t("settings.server.tls.key"), h("span", { class: "mono small" }, info.keyfile || t("common.dash"))],
          [t("settings.server.tls.fingerprint"), h("span", { class: "mono small" }, info.fingerprint || t("common.dash"))],
          [t("settings.server.tls.expires"), info.not_after || t("common.dash")],
        ]),
        h("p", { class: "small muted" }, t("settings.server.tls.trustHint")),
        h("div", { class: "toolbar" },
          button(info.exists ? t("settings.server.tls.regenerate") : t("settings.server.tls.generate"), generate,
            { kind: "primary", size: "sm" }),
          info.exists && !tls.enabled ? button(t("settings.server.tls.enable"), enable, { size: "sm", kind: "ghost" }) : null,
          tls.enabled ? button(t("settings.server.tls.disable"), disable, {
            size: "sm", kind: "ghost", disabled: external,
            title: external ? t("settings.server.tls.externalWarning") : "",
          }) : null
        )
      )),

      card(t("settings.dirs.card"), h("div", {},
        kvRows([
          [t("settings.dirs.home"), h("span", { class: "mono small" }, paths.home || t("common.dash"))],
          [t("settings.dirs.config"), h("span", { class: "mono small" }, paths.config || t("common.dash"))],
          [t("settings.dirs.audit"), h("span", { class: "mono small" }, paths.audit || t("common.dash"))],
          [t("settings.dirs.files"), fmtNumber(workspace.files, 0)],
          [t("settings.dirs.size"), workspace.size_human || fmtBytes(workspace.size)],
        ]),
        h("p", { class: "small muted" }, t("settings.dirs.note"))))
    );
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: мониторинг                                                */
  /* ------------------------------------------------------------------ */

  const drawMonitor = (host) => {
    const monitor = data.monitor || {};
    const thresholds = monitor.thresholds || {};
    const grid = h("div", { class: "grid cards-2" });

    mount(grid, THRESHOLDS.map(([key, labelKey, fallback]) => {
      const label = t(labelKey);
      return numberField(label, thresholds[key] === undefined ? fallback : thresholds[key], {
        min: 0, step: key === "load_per_core" ? 0.1 : 1,
        onSave: async (value) => {
          await save({ monitor: { thresholds: { [key]: value } } }, { success: t("settings.thresholds.saved", { label }) });
        },
      });
    }));

    mount(host,
      card(t("settings.monitor.card"), h("div", { class: "col" },
        h("div", { class: "grid cards-2" },
          numberField(t("settings.monitor.interval"), monitor.interval_ms, {
            min: 500, max: 60000, step: 100, hint: t("settings.monitor.intervalHint"),
            onSave: async (value) => { await save({ monitor: { interval_ms: value } }, { success: t("settings.monitor.intervalSaved") }); },
          }),
          numberField(t("settings.monitor.history"), monitor.history_points, {
            min: 60, max: 100000, step: 60, hint: t("settings.monitor.historyHint"),
            onSave: async (value) => { await save({ monitor: { history_points: value } }, { success: t("settings.monitor.historySaved") }); },
          }),
          numberField(t("settings.monitor.containerPoll"), monitor.container_poll_sec, {
            min: 5, max: 600, step: 5, hint: t("settings.monitor.containerPollHint"),
            onSave: async (value) => { await save({ monitor: { container_poll_sec: value } }, { success: t("settings.monitor.pollSaved") }); },
          })),
      )),

      card(t("settings.thresholds.title"), grid),

      card(t("settings.monitor.watchCard"), h("div", { class: "col" },
        linesField("monitor.watch_paths", monitor.watch_paths, t("settings.monitor.watchHint"),
          async (list) => { await save({ monitor: { watch_paths: list } }, { success: t("settings.monitor.watchSaved") }); }))),

      card(t("settings.monitor.rootsCard"), h("div", { class: "col" },
        linesField("monitor.extra_roots", monitor.extra_roots, t("settings.monitor.rootsHint"),
          async (list) => { await save({ monitor: { extra_roots: list } }, { success: t("settings.monitor.rootsSaved") }); })))
    );
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: движки                                                    */
  /* ------------------------------------------------------------------ */

  const drawEngines = (host) => {
    const engines = data.engines || {};
    const reportHost = h("div");

    const drawReport = () => {
      if (!engineReport) {
        mount(reportHost, h("p", { class: "small muted" }, t("settings.engines.empty")));
        return;
      }
      const rows = [];
      for (const [name, info] of Object.entries((engineReport.engines && engineReport.engines.engines) || {})) {
        rows.push({
          name,
          available: info.available,
          binary: info.binary || t("common.dash"),
          version: (info.version || "").split("\n")[0],
          error: info.error || "",
        });
      }
      const extra = [
        ["kubectl", engineReport.kubectl],
        ["nginx", engineReport.nginx],
        ["ansible", engineReport.ansible],
      ];
      mount(reportHost,
        rows.length
          ? table({
              columns: [
                { key: "name", label: t("settings.engines.columnEngine") },
                { key: "available", label: t("settings.engines.columnFound"), render: (row) => row.available ? badge(t("common.buttons.yes"), "ok") : badge(t("common.buttons.no"), "muted") },
                { key: "binary", label: t("settings.engines.columnPath"), render: (row) => h("span", { class: "mono small" }, row.binary) },
                { key: "version", label: t("settings.engines.columnVersion") },
                { key: "error", label: t("settings.engines.columnNote"), render: (row) => h("span", { class: "small muted" }, row.error || t("common.dash")) },
              ],
              rows,
            })
          : notice(t("settings.engines.noData"), "warn"),
        h("div", { class: "grid cards-3", style: { marginTop: "12px" } }, extra.map(([label, info]) =>
          h("div", { class: "stat" },
            h("div", { class: "stat-label" }, label),
            h("div", { class: "stat-value" }, info && info.available ? t("settings.engines.found") : t("settings.engines.missing")),
            h("div", { class: "stat-detail" }, (info && (info.binary || info.version)) || (info && info.error) || "")))),
        h("p", { class: "small muted", style: { marginTop: "10px" } },
          t("settings.engines.checked", { time: engineReport.checked || t("common.dash") })));
    };

    drawReport();

    mount(host,
      card(t("settings.engines.title"), h("div", { class: "col" },
        field(t("settings.engines.engine"), select(
          CONTAINER_ENGINES.map((item) => ({ value: item.value, label: item.key ? t(item.key) : item.value })),
          {
            value: engines.preferred_container_engine || "auto",
            onchange: (event) => save({ engines: { preferred_container_engine: event.target.value } }, { success: t("settings.engines.engineSaved") }),
          }), t("settings.engines.engineHint")))),

      card(t("settings.engines.paths"), h("div", { class: "row-wrap grid cards-2" },
        ENGINES.map(([key, label, hintKey]) =>
          textField(label, engines[key], t(hintKey), async (value) => {
            await save({ engines: { [key]: value } }, { success: t("settings.engines.pathSaved", { name: label }) });
          }, { placeholder: t("settings.engines.pathPlaceholder") })))),

      card(t("settings.engines.test"), h("div", {},
        toolbar(button(t("settings.engines.check"), async () => {
          mount(reportHost, spinner(t("settings.engines.testing")));
          const result = await guard(() => api.post("/api/settings/engines/test"), { quiet: true });
          if (result && result.data) {
            engineReport = result.data;
            toast(t("settings.engines.testDone"), "success");
          } else {
            mount(reportHost, notice(t("settings.engines.testFailed"), "bad"));
          }
          drawReport();
        }, { kind: "primary" })),
        reportHost))
    );
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: безопасность                                              */
  /* ------------------------------------------------------------------ */

  const drawSafety = (host) => {
    const safety = data.safety || {};
    mount(host,
      card(t("settings.safety.card"), h("div", { class: "col" },
        notice(t("settings.safety.notice"), "warn"),
        SAFETY.map(([key, labelKey, hintKey]) =>
          h("label", { class: "check", style: { alignItems: "flex-start" } },
            h("input", {
              type: "checkbox",
              checked: safety[key] !== false,
              onchange: (event) => save({ safety: { [key]: event.target.checked } }, { success: t("settings.settingSaved") }),
            }),
            h("span", {}, h("div", {}, t(labelKey)), h("div", { class: "small muted" }, t(hintKey))))))));
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: система                                                   */
  /* ------------------------------------------------------------------ */

  const drawSystem = (host) => {
    const audit = data.audit || {};
    const jobs = data.jobs || {};
    mount(host,
      card(t("settings.system.auditCard"), h("div", { class: "col" },
        notice(t("settings.system.auditNotice"), "warn"),
        h("div", { class: "grid cards-2" },
          numberField(t("settings.system.auditMax"), audit.max_mb ?? 5, {
            min: 1, max: 1024, step: 0.5, hint: t("settings.system.auditMaxHint"),
            onSave: async (value) => { await save({ audit: { max_mb: value } }, { success: t("settings.system.auditSaved") }); },
          }),
          numberField(t("settings.system.auditKeep"), audit.keep ?? 3, {
            min: 1, max: 50, hint: t("settings.system.auditKeepHint"),
            onSave: async (value) => { await save({ audit: { keep: value } }, { success: t("settings.system.auditSaved") }); },
          })))),

      card(t("settings.system.jobsCard"), h("div", { class: "col" },
        notice(t("settings.system.jobsNotice"), "info"),
        h("div", { class: "grid cards-2" },
          numberField(t("settings.system.jobsActive"), jobs.max_active ?? 8, {
            min: 1, max: 64, hint: t("settings.system.jobsActiveHint"),
            onSave: async (value) => { await save({ jobs: { max_active: value } }, { success: t("settings.system.jobsSaved") }); },
          }),
          numberField(t("settings.system.jobsKeep"), jobs.keep ?? 60, {
            min: 10, max: 500, step: 10, hint: t("settings.system.jobsKeepHint"),
            onSave: async (value) => { await save({ jobs: { keep: value } }, { success: t("settings.system.jobsSaved") }); },
          })))));
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: интерфейс                                                 */
  /* ------------------------------------------------------------------ */

  const drawInterface = (host) => {
    // Локальная копия: пользователь может переключать плотность
    // быстро, а ответы сервера приходят уже после — поэтому храним текущий
    // выбор в одном объекте, а не читаем из устаревшего data.ui.
    const ui = Object.assign({ theme: "light", density: "normal", language: "ru", notifications: false }, data.ui || {});

    const applyDensity = () => {
      document.documentElement.dataset.density = ui.density === "compact" ? "compact" : "normal";
      window.dispatchEvent(new Event("megatool:density"));
    };

    mount(host,
      card(t("settings.ui.appearance"), h("div", { class: "col" },
        field(t("settings.ui.theme"), select([
          { value: "light", label: t("settings.ui.themeLight") },
          { value: "sepia", label: t("settings.ui.themeSepia") },
          { value: "slate", label: t("settings.ui.themeSlate") },
          { value: "dark", label: t("settings.ui.themeDark") },
          { value: "contrast", label: t("settings.ui.themeContrast") },
          { value: "auto", label: t("settings.ui.themeAuto") },
        ], {
          value: ui.theme,
          onchange: async (event) => {
            ui.theme = event.target.value;
            await save({ ui: { theme: ui.theme } }, { success: t("settings.ui.themeSaved") });
            // Тему применяет app.js по этому событию — локально не дублируем.
            window.dispatchEvent(new CustomEvent("megatool:theme-set", { detail: { theme: ui.theme } }));
          },
        }), t("settings.ui.themeHint")),
        field(t("settings.ui.density"), select([
          { value: "normal", label: t("settings.ui.densityNormal") },
          { value: "compact", label: t("settings.ui.densityCompact") },
        ], {
          value: ui.density,
          onchange: async (event) => {
            ui.density = event.target.value;
            applyDensity();
            await save({ ui: { density: ui.density } }, { success: t("settings.ui.densitySaved") });
          },
        }), t("settings.ui.densityHint")),
        field(t("settings.ui.language"), select([
          { value: "ru", label: t("settings.ui.langRu") },
          { value: "en", label: t("settings.ui.langEn") },
        ], {
          value: ui.language,
          onchange: async (event) => {
            const value = event.target.value;
            const message = t("settings.ui.langSaved");
            setLang(value);
            await guard(() => api.patch("/api/settings", { ui: { language: value } }), { success: message });
            location.reload();
          },
        }), t("settings.ui.languageHint")))),

      card(t("settings.ui.notifyCard"), h("div", { class: "col" },
        h("label", { class: "check", style: { alignItems: "flex-start" } },
          h("input", {
            type: "checkbox",
            checked: Boolean(ui.notifications),
            onchange: async (event) => {
              if (event.target.checked) {
                let granted = false;
                if (typeof Notification !== "undefined" && typeof Notification.requestPermission === "function") {
                  try {
                    granted = (await Notification.requestPermission()) === "granted";
                  } catch (error) {
                    granted = false;
                  }
                }
                if (!granted) {
                  event.target.checked = false;
                  toast(t("settings.ui.notifyDenied"), "error");
                  return;
                }
              }
              ui.notifications = event.target.checked;
              const saved = await save({ ui: { notifications: ui.notifications } }, { success: t("settings.settingSaved") });
              // app.js проверяет app.settings.ui.notifications при каждом алерте —
              // обновляем копию, чтобы настройка действовала без перезагрузки.
              if (saved && app && app.settings && app.settings.ui) app.settings.ui.notifications = ui.notifications;
            },
          }),
          h("span", {}, h("div", {}, t("settings.ui.notifyLabel")), h("div", { class: "small muted" }, t("settings.ui.notifyHint")))))));
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: данные                                                    */
  /* ------------------------------------------------------------------ */

  const drawData = (host) => {
    const importer = h("input", { type: "file", accept: ".json,application/json" });
    const output = h("pre", { class: "console", style: { height: "220px" }, hidden: true });

    const refreshPaths = async () => {
      const result = await guard(() => api.get("/api/paths"), { quiet: true });
      if (result && result.data) paths = Object.assign({}, paths, result.data);
    };

    importer.onchange = async () => {
      const file = importer.files && importer.files[0];
      if (!file) return;
      let parsed = null;
      try {
        parsed = JSON.parse(await file.text());
      } catch (error) {
        toast(t("settings.data.badJson"), "error");
        importer.value = "";
        return;
      }
      const ok = await confirmDialog(t("settings.data.confirm"),
        { title: t("settings.data.confirmTitle"), okLabel: t("settings.data.confirmOk"), danger: false });
      if (!ok) {
        importer.value = "";
        return;
      }
      const result = await guard(() => api.post("/api/settings/import", { config: parsed }), { success: t("settings.data.imported") });
      if (result) {
        await load();
        draw();
      }
      importer.value = "";
    };

    mount(host,
      card(t("settings.data.exportCard"), h("div", { class: "col" },
        h("div", { class: "toolbar" },
          button(t("settings.data.downloadJson"), async () => {
            const result = await guard(() => api.get("/api/settings/export"), { quiet: true });
            if (!result) return;
            const text = JSON.stringify(result.data || {}, null, 2);
            output.hidden = false;
            output.textContent = text;
            downloadJson("megatool-settings.json", text);
          }, { kind: "primary" }),
          button(t("settings.data.importFile"), () => importer.click(), { kind: "ghost" }),
          button(t("settings.data.showRaw"), async () => {
            const result = await guard(() => api.get("/api/settings/export"), { quiet: true });
            if (!result) return;
            output.hidden = false;
            output.textContent = JSON.stringify(result.data || {}, null, 2);
          }, { kind: "ghost" })),
        importer,
        notice(t("settings.data.redactNote"), "warn"),
        output)),

      card(t("settings.data.dirsCard"), h("div", {},
        h("div", { class: "toolbar" }, button(t("settings.data.refresh"), async () => { await refreshPaths(); await load(); draw(); }, { kind: "ghost", size: "sm" })),
        kvRows([
          [t("settings.data.home"), h("span", { class: "mono small" }, paths.home || t("common.dash"))],
          [t("settings.data.apps"), h("span", { class: "mono small" }, paths.apps || t("common.dash"))],
          [t("settings.data.configs"), h("span", { class: "mono small" }, paths.configs || t("common.dash"))],
          [t("settings.data.runs"), h("span", { class: "mono small" }, paths.runs || t("common.dash"))],
          [t("settings.data.logs"), h("span", { class: "mono small" }, paths.logs || t("common.dash"))],
        ]))));
  };

  const downloadJson = (filename, text) => {
    const blob = new Blob([text], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладка: агенты мониторинга                                        */
  /* ------------------------------------------------------------------ */

  const drawAgents = (host) => {
    const agents = Array.isArray(data.agents) ? data.agents : [];
    const statuses = new Map();
    const statusHost = h("div");

    const loadText = (agent) => {
      const status = statuses.get(agent.id);
      if (!status) return h("span", { class: "muted small" }, t("settings.agents.noData"));
      if (!status.online) return h("span", { class: "small muted", title: status.error || "" }, t("settings.agents.offline"));
      const metrics = status.metrics || {};
      return h("span", { class: "mono small" }, t("settings.agents.loadLine", {
        cpu: fmtNumber(metrics.cpu_pct),
        mem: fmtNumber(metrics.mem_pct),
        disk: fmtNumber(metrics.disk_pct),
      }));
    };

    const renderStatuses = () => {
      if (!agents.length) {
        mount(statusHost, empty(t("settings.agents.empty"), {
          mark: "◉",
          hint: t("settings.agents.emptyHint"),
          extra: h("pre", { class: "mono-block", style: { textAlign: "left", marginTop: "12px" } },
            "python3 megatool-agent.py --host 0.0.0.0 --token SECRET"),
        }));
        return;
      }
      mount(statusHost, table({
        columns: [
          {
            key: "status", label: t("settings.agents.col.status"), render: (row) => {
              const status = statuses.get(row.id);
              if (!status) return badge("—", "muted");
              return badge(status.online ? t("settings.agents.online") : t("settings.agents.offline"),
                status.online ? "ok" : "bad", status.error || "");
            },
          },
          {
            key: "name", label: t("settings.agents.col.name"), render: (row) => h("div", {},
              h("div", {}, row.name),
              h("div", { class: "mono small muted" }, row.url)),
          },
          {
            key: "token", label: t("settings.agents.col.token"), render: (row) =>
              badge(row.token_set ? t("settings.agents.tokenSet") : t("settings.agents.tokenNone"), "muted"),
          },
          { key: "load", label: t("settings.agents.col.load"), render: loadText },
          {
            key: "actions", label: "", align: "right", render: (row) => h("div", { class: "toolbar" },
              button(t("settings.agents.test"), () => testAgent(row), { size: "xs", kind: "ghost" }),
              button(t("common.buttons.edit"), () => editDialog(row), { size: "xs", kind: "ghost" }),
              button(t("common.buttons.delete"), () => removeAgent(row), { size: "xs", kind: "ghost" })),
          },
        ],
        rows: agents,
      }));
    };

    const refresh = async (force = false) => {
      if (!agents.length) {
        renderStatuses();
        return;
      }
      mount(statusHost, spinner(t("settings.agents.checking")));
      const result = await guard(() => api.get("/api/agents", force ? { force: 1 } : {}), { quiet: true });
      if (!result) {
        mount(statusHost, notice(t("settings.agents.statusFailed"), "bad"));
        return;
      }
      statuses.clear();
      for (const item of (result.data || {}).agents || []) {
        if (item && item.id) statuses.set(item.id, item);
      }
      renderStatuses();
    };

    const persist = async (next, success) => {
      const payload = next.map(({ id, name, url, token }) => ({ id: id || "", name, url, token: token || "" }));
      const ok = await save({ agents: payload }, { success });
      if (ok) draw();
      return ok;
    };

    const testAgent = async (agent) => {
      const body = agent && agent.id ? { id: agent.id, url: agent.url } : { url: agent.url, token: agent.token || "" };
      const result = await guard(() => api.post("/api/agents/test", body), { success: "" });
      if (!result) return;
      const metrics = result.data || {};
      toast(t("settings.agents.testOk", {
        cpu: fmtNumber(metrics.cpu_pct),
        mem: fmtNumber(metrics.mem_pct),
        disk: fmtNumber(metrics.disk_pct),
        host: metrics.host || "",
      }), "success", 6000);
    };

    const removeAgent = async (agent) => {
      const ok = await confirmDialog(t("settings.agents.deleteBody", { name: agent.name }), {
        title: t("settings.agents.deleteTitle"),
        okLabel: t("common.buttons.delete"),
      });
      if (!ok) return;
      await persist(agents.filter((item) => item.id !== agent.id), t("settings.agents.removed"));
    };

    const editDialog = (agent = null) => {
      const nameInput = input({ value: agent ? agent.name : "", placeholder: t("settings.agents.namePlaceholder") });
      const urlInput = input({ value: agent ? agent.url : "", placeholder: "http://192.168.1.10:8791" });
      const tokenInput = input({
        type: "password",
        value: "",
        autocomplete: "new-password",
        placeholder: agent && agent.token_set ? t("settings.agents.tokenKeep") : "",
      });
      const testHost = h("div", { class: "col" });
      const checkButton = button(t("settings.agents.test"), async () => {
        const url = urlInput.value.trim();
        if (!url) {
          toast(t("settings.agents.required"), "warn");
          return;
        }
        mount(testHost, spinner(t("settings.agents.testing")));
        // Для сохранённого агента пустой токен означает «взять сохранённый»:
        // так проверка работает и после смены адреса без повторного ввода секрета.
        const body = agent
          ? { id: agent.id, url, token: tokenInput.value }
          : { url, token: tokenInput.value };
        const result = await guard(() => api.post("/api/agents/test", body), { success: "" });
        if (!result) {
          mount(testHost, null);
          return;
        }
        const metrics = result.data || {};
        mount(testHost, notice(t("settings.agents.testOk", {
          cpu: fmtNumber(metrics.cpu_pct),
          mem: fmtNumber(metrics.mem_pct),
          disk: fmtNumber(metrics.disk_pct),
          host: metrics.host || "",
        }), "ok"));
      }, { size: "sm", kind: "ghost" });

      modal({
        title: agent ? t("settings.agents.edit") : t("settings.agents.add"),
        width: 560,
        body: h("div", {},
          field(t("settings.agents.name"), nameInput, t("settings.agents.nameHint")),
          field(t("settings.agents.url"), urlInput, t("settings.agents.urlHint")),
          field(t("settings.agents.token"), tokenInput, t("settings.agents.tokenHint")),
          testHost,
          h("div", { class: "toolbar" }, checkButton)),
        actions: [
          { label: t("common.buttons.cancel") },
          {
            label: t("common.buttons.save"), kind: "primary", onClick: async (close) => {
              const name = nameInput.value.trim();
              const url = urlInput.value.trim();
              if (!name || !url) {
                toast(t("settings.agents.required"), "warn");
                return;
              }
              const next = agents.map((item) => ({ ...item }));
              if (agent) {
                const index = next.findIndex((item) => item.id === agent.id);
                if (index >= 0) next[index] = { ...next[index], name, url, token: tokenInput.value };
              } else {
                next.push({ id: "", name, url, token: tokenInput.value });
              }
              if (await persist(next, t("settings.agents.saved"))) close();
            },
          },
        ],
      });
    };

    mount(host, card(t("settings.agents.card"), h("div", { class: "col" },
      h("div", { class: "small muted" }, t("settings.agents.intro")),
      statusHost
    ), {
      extra: toolbar(
        button(t("common.buttons.refresh"), () => refresh(true), { size: "sm", kind: "ghost" }),
        button(t("settings.agents.add"), () => editDialog(), { size: "sm", kind: "primary" }),
      ),
    }));

    refresh();
  };

  /* ------------------------------------------------------------------ */
  /*  Разводка                                                           */
  /* ------------------------------------------------------------------ */

  const tabBar = tabs(
    [
      { id: "server", label: t("settings.tabs.server") },
      { id: "monitor", label: t("settings.tabs.monitor") },
      { id: "agents", label: t("settings.tabs.agents") },
      { id: "engines", label: t("settings.tabs.engines") },
      { id: "safety", label: t("settings.tabs.safety") },
      { id: "system", label: t("settings.tabs.system") },
      { id: "ui", label: t("settings.tabs.ui") },
      { id: "data", label: t("settings.tabs.data") },
    ],
    { active: activeTab, onChange: (id) => { activeTab = id; draw(); } }
  );

  const draw = () => {
    const host = h("div", { class: "col" });
    if (!data) {
      mount(bodyHost, empty(t("settings.unavailable"), { mark: "⚙", hint: t("settings.unavailableHint") }));
      return;
    }
    if (activeTab === "server") drawServer(host);
    else if (activeTab === "monitor") drawMonitor(host);
    else if (activeTab === "agents") drawAgents(host);
    else if (activeTab === "engines") drawEngines(host);
    else if (activeTab === "safety") drawSafety(host);
    else if (activeTab === "system") drawSystem(host);
    else if (activeTab === "ui") drawInterface(host);
    else drawData(host);
    mount(bodyHost, host);
  };

  mount(root, head, h("div", { class: "col" }, tabBar, bodyHost));
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, t("settings.title")),
      h("div", { class: "view-sub" }, t("settings.subtitle"))),
    h("div", { class: "toolbar" },
      button(t("settings.openJobs"), () => (location.hash = "#/jobs"), { kind: "ghost" }),
      button(t("settings.download"), async () => {
        const result = await guard(() => api.get("/api/settings/export"), { quiet: true });
        if (result) downloadJson("megatool-settings.json", JSON.stringify(result.data || {}, null, 2));
      }, { kind: "ghost" })));

  mount(bodyHost, spinner());
  const ok = await load();
  if (!ok) mount(bodyHost, empty(t("settings.loadFailed"), { mark: "⚙" }));
  else draw();

  return () => cleanups.forEach((stop) => stop && stop());
}
