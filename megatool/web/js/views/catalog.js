/* Каталог приложений: рецепты (compose-стеки, k8s-манифесты, конфиги),
   развёртывание с генерацией секретов и управление развёрнутым. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, card, confirmDialog, empty, field, guard, h, input, jobModal, kvRows, modal,
  mount, notice, poller, select, stateBadge, table, tabs, toast, toolbar,
} from "../ui.js";

const KIND_HINTS = {
  compose: "catalog.kind.compose",
  manifest: "catalog.kind.manifest",
  config: "catalog.kind.config",
};

export async function render(root) {
  const cleanups = [];
  let activeTab = "recipes";
  let contentGeneration = 0;
  let query = "";
  let category = "";
  let kind = "";
  let recipes = [];
  let categories = {};
  let engineSummary = null;

  try {
    engineSummary = (await api.get("/api/engines")).data;
  } catch (error) {
    engineSummary = null;
  }

  const head = h("div", { class: "view-head" });
  const tabHost = h("div");
  const bodyHost = h("div");

  /* ------------------------------------------------------------------ */
  /*  Каталог рецептов                                                   */
  /* ------------------------------------------------------------------ */

  const loadRecipes = async () => {
    const generation = (contentGeneration += 1);
    const result = await guard(() => api.get("/api/catalog/recipes", { q: query, category, kind }), { quiet: true });
    if (!result || generation !== contentGeneration) return;
    recipes = result.items || [];
    categories = result.categories || categories;
    drawRecipes();
  };

  const drawRecipes = () => {
    mount(bodyHost,
      h("div", { class: "chips", style: { marginBottom: "12px" } },
        h("button", { type: "button", class: `chip ${category === "" ? "active" : ""}`, onclick: () => { category = ""; loadRecipes(); } }, t("catalog.filters.allCategories")),
        Object.entries(categories).map(([id, label]) =>
          h("button", { type: "button", class: `chip ${category === id ? "active" : ""}`, onclick: () => { category = id; loadRecipes(); } }, label)),
        h("span", { class: "grow" }),
        ["", "compose", "manifest", "config"].map((id) =>
          h("button", { type: "button", class: `chip ${kind === id ? "active" : ""}`, onclick: () => { kind = id; loadRecipes(); } }, id || t("catalog.filters.anyKind")))),
      recipes.length
        ? h("div", { class: "grid cards-3" }, recipes.map((recipe) =>
            card(recipe.name, h("div", { class: "col" },
              h("div", { class: "row row-wrap" },
                badge(recipe.kind_label, "neutral"),
                badge(recipe.category_label, "muted"),
                recipe.tags.slice(0, 4).map((tag) => h("span", { class: "tag" }, tag))),
              h("p", { class: "small", style: { margin: "6px 0" } }, recipe.description),
              recipe.docs ? h("details", { class: "raw" }, h("summary", {}, t("catalog.recipe.docs")), h("pre", { class: "mono-block" }, recipe.docs)) : null,
              recipe.warning ? notice(recipe.warning, "warn") : null,
              h("div", { class: "muted small" }, t("catalog.recipe.files", { list: (recipe.files || []).join(", ") })),
              h("div", { class: "toolbar", style: { marginTop: "8px" } },
                button(t("catalog.deploy.action"), () => deployDialog(recipe), { kind: "primary", size: "sm" }),
                button(t("catalog.recipe.showFiles"), () => previewRecipe(recipe), { size: "sm", kind: "ghost" })))
            , { class: "card" })))
        : empty(t("catalog.empty.title"), { mark: "▦", hint: t("catalog.empty.hint") }));
  };

  const previewRecipe = async () => {
    toast(t("catalog.recipe.previewToast"), "info");
  };

  /* ------------------------------------------------------------------ */
  /*  Развёртывание                                                      */
  /* ------------------------------------------------------------------ */

  const deployDialog = (recipe) => {
    const valueInputs = {};
    const fields = [];

    for (const variable of recipe.vars || []) {
      const control = input({ value: variable.default || "" });
      valueInputs[variable.key] = control;
      fields.push(field(variable.label || variable.key, control, variable.hint || ""));
    }

    const nameField = (recipe.vars || []).some((item) => item.key === "NAME")
      ? null
      : (() => {
          const control = input({ value: recipe.id });
          valueInputs.__name = control;
          return field(t("catalog.deploy.name"), control, t("catalog.deploy.nameHint"));
        })();

    const engineOptions = [{ value: "", label: t("catalog.deploy.engineAuto") }, { value: "docker", label: "docker" }, { value: "podman", label: "podman" }];
    const engineSelect = select(engineOptions, { value: (engineSummary && engineSummary.active) || "" });
    const startCheck = h("input", { type: "checkbox", checked: recipe.kind !== "config" });
    const startLabel = recipe.kind === "manifest" ? t("catalog.deploy.startManifest") : recipe.kind === "compose" ? t("catalog.deploy.startCompose") : t("catalog.deploy.startNone");

    for (const variable of recipe.env || []) {
      const control = input({ value: "", type: variable.secret ? "password" : "text", placeholder: variable.secret ? t("catalog.deploy.secretPlaceholder") : (variable.default || "") });
      valueInputs[variable.key] = control;
      fields.push(field(variable.label || variable.key, control, variable.secret ? t("catalog.deploy.secretHint") : (variable.hint || "")));
    }

    modal({
      title: t("catalog.deploy.title", { name: recipe.name }),
      body: h("div", {},
        h("p", { class: "small muted" }, KIND_HINTS[recipe.kind] ? t(KIND_HINTS[recipe.kind]) : recipe.kind),
        recipe.warning ? notice(recipe.warning, "warn") : null,
        h("div", { class: "split" }, fields.length ? h("div", {}, fields) : h("div", {}, h("p", {}, t("catalog.deploy.noParams"))),
          h("div", {},
            nameField,
            field(t("catalog.deploy.engine"), engineSelect, t("catalog.deploy.engineHint")),
            recipe.kind !== "config" ? h("label", { class: "check" }, startCheck, startLabel) : null,
            recipe.apply_hint ? h("p", { class: "small muted" }, recipe.apply_hint) : null))),
      width: 780,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("catalog.deploy.action"), kind: "primary",
          onClick: async (close) => {
            const values = {};
            for (const [key, control] of Object.entries(valueInputs)) {
              if (key === "__name") continue;
              values[key] = control.value;
            }
            const result = await guard(() => api.post("/api/catalog/deploy", {
              recipe_id: recipe.id,
              name: valueInputs.__name ? valueInputs.__name.value.trim() : "",
              values,
              start: startCheck.checked && recipe.kind !== "config",
              engine: engineSelect.value,
            }), { quiet: true });
            if (!result) return;
            if (result.ok === false) {
              toast(result.error || t("catalog.deploy.failed"), "error", 7000);
              return;
            }
            close();
            deployResultDialog(result);
            if (activeTab === "deployed") loadDeployed();
          },
        },
      ],
    });
  };

  const deployResultDialog = (result) => {
    modal({
      title: t("catalog.deploy.deployedOk"),
      body: h("div", {},
        kvRows([
          [t("catalog.labels.dir"), result.dir],
          [t("catalog.labels.kind"), result.kind],
          [t("catalog.labels.files"), (result.files || []).join(", ")],
          [t("catalog.labels.ports"), (result.ports || []).join(", ") || t("common.dash")],
        ]),
        (result.secrets_created || []).length
          ? notice(t("catalog.deploy.secrets", { list: result.secrets_created.join(", ") }), "ok")
          : null,
        result.warning ? notice(result.warning, "warn") : null,
        result.apply_hint ? h("p", { class: "small muted" }, result.apply_hint) : null,
        result.start ? h("div", {}, h("h4", { class: "small" }, t("catalog.deploy.startHeading")), h("pre", { class: "mono-block" }, JSON.stringify(result.start, null, 2))) : null,
        result.apply ? h("div", {}, h("pre", { class: "mono-block" }, JSON.stringify(result.apply.results || result.apply, null, 2))) : null),
      width: 720,
      actions: [
        { label: t("catalog.deploy.openEditor"), onClick: () => { sessionStorage.setItem("megatool.openPath", result.dir); location.hash = "#/configs"; } },
        { label: t("catalog.deploy.toDeployed"), onClick: () => { tabBar.setActive("deployed"); activeTab = "deployed"; drawTab(); } },
        { label: t("common.buttons.close") },
      ],
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Развёрнутые приложения                                             */
  /* ------------------------------------------------------------------ */

  const deployedHost = h("div");

  const loadDeployed = async () => {
    const generation = (contentGeneration += 1);
    const result = await guard(() => api.get("/api/catalog/deployed"), { quiet: true });
    if (!result || generation !== contentGeneration) return;
    const items = result.items || [];
    const stats = result.stats || {};
    mount(deployedHost, items.length
      ? h("div", {},
          h("div", { class: "chips", style: { marginBottom: "12px" } },
            badge(t("catalog.deployed.total", { n: stats.total ?? 0 }), "muted"),
            badge(`compose ${stats.compose ?? 0}`, "neutral"),
            badge(t("catalog.deployed.manifests", { n: stats.manifest ?? 0 }), "neutral"),
            badge(t("catalog.deployed.configs", { n: stats.config ?? 0 }), "neutral")),
          h("div", { class: "grid cards-2" }, items.map((item) =>
            card(item.name || item.slug, h("div", { class: "col" },
              h("div", { class: "row row-wrap" },
                badge(item.kind, "neutral"),
                badge(item.recipe_name || item.recipe, "muted"),
                item.exists ? badge(t("catalog.deployed.onDisk"), "ok") : badge(t("catalog.deployed.dirMissing"), "bad"),
                item.kind === "compose" && item.total ? badge(t("catalog.deployed.services", { running: item.running, total: item.total }), item.running === item.total ? "ok" : "warn") : null,
                item.secrets_created && item.secrets_created.length ? badge(t("catalog.deployed.secrets", { n: item.secrets_created.length }), "warn") : null),
              h("div", { class: "mono small muted" }, item.dir),
              h("div", { class: "small muted" }, t("catalog.deployed.meta", { time: (item.created_iso || "").replace("T", " ").slice(0, 19), files: item.files_count ?? 0, size: item.size_human || t("common.dash") })),
              item.status_error ? notice(item.status_error, "bad") : null,
              (item.status || []).length
                ? table({
                    columns: [
                      { key: "service", label: t("catalog.labels.service") },
                      { key: "state", label: t("catalog.labels.state"), render: (row) => stateBadge(row.state) },
                      { key: "ports", label: t("catalog.labels.ports"), render: (row) => h("span", { class: "mono small" }, (row.ports || []).join(", ") || t("common.dash")) },
                    ],
                    rows: item.status,
                  })
                : null,
              h("div", { class: "toolbar", style: { marginTop: "8px" } },
                item.kind === "compose" ? ["up", "restart", "down"].map((action) =>
                  button(action, () => appAction(item, action), { size: "sm", kind: action === "down" ? "danger" : "ghost" })) : null,
                item.kind === "compose" ? button("logs", () => showLogs(item), { size: "sm", kind: "ghost" }) : null,
                item.kind === "manifest" ? button("apply (dry-run)", () => applyManifests(item, true), { size: "sm", kind: "ghost" }) : null,
                item.kind === "manifest" ? button("apply", () => applyManifests(item, false), { size: "sm", kind: "ghost" }) : null,
                button(t("catalog.actions.editor"), () => { sessionStorage.setItem("megatool.openPath", item.dir); location.hash = "#/configs"; }, { size: "sm", kind: "ghost" }),
                button("CSV/JSON", () => exportItem(item), { size: "sm", kind: "ghost" }),
                button(t("common.buttons.delete"), () => removeDialog(item), { size: "sm", kind: "danger" }))))
          , { class: "card" })))
      : empty(t("catalog.deployed.empty"), { mark: "▦", hint: t("catalog.deployed.emptyHint") }));
  };

  const appAction = async (item, action) => {
    if (action === "down" && !(await confirmDialog(t("catalog.confirmDown", { name: item.name })))) return;
    const result = await guard(() => api.post(`/api/catalog/deployed/${encodeURIComponent(item.slug)}/${action}`), { quiet: true });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("catalog.actionFailed"), "error");
      return;
    }
    const job = result.data && result.data.job ? result.data.job : result.job;
    if (job) jobModal(job, { title: `${item.name}: ${action}` });
    setTimeout(loadDeployed, 3000);
  };

  const showLogs = async (item) => {
    const result = await guard(() => api.get(`/api/catalog/deployed/${encodeURIComponent(item.slug)}/logs`, { tail: 300 }), { quiet: true });
    modal({
      title: t("catalog.logs.title", { name: item.name }),
      body: h("pre", { class: "mono-block", style: { minHeight: "280px", maxHeight: "60vh" } }, (result && result.text) || (result && result.error) || t("catalog.emptyText")),
      width: 900,
      actions: [{ label: t("common.buttons.close") }],
    });
  };

  const applyManifests = async (item, dryRun) => {
    const result = await guard(() => api.post(`/api/catalog/deployed/${encodeURIComponent(item.slug)}/apply`, {}, { dry_run: dryRun }), { quiet: true });
    if (!result) return;
    if (result.ok === false && result.hint) {
      toast(result.hint, "error", 8000);
      return;
    }
    modal({
      title: t(dryRun ? "catalog.apply.checkTitle" : "catalog.apply.applyTitle", { name: item.name }),
      body: h("div", {}, (result.results || []).map((entry) =>
        h("div", { style: { marginBottom: "10px" } },
          h("div", { class: "row" }, badge(entry.file, "neutral"), stateBadge(entry.ok ? "success" : "failed", entry.ok ? t("common.status.success") : t("common.status.failed"))),
          h("pre", { class: "mono-block" }, (entry.out || entry.err || entry.error || "").slice(0, 4000) || t("catalog.emptyText"))))),
      width: 860,
      actions: [{ label: t("common.buttons.close") }],
    });
  };

  const exportItem = (item) => {
    modal({
      title: t("catalog.export.title", { name: item.name }),
      body: h("pre", { class: "mono-block", style: { maxHeight: "60vh" } }, JSON.stringify(item, null, 2)),
      width: 780,
      actions: [{
        label: t("catalog.export.copyJson"), onClick: async () => {
          try {
            await navigator.clipboard.writeText(JSON.stringify(item, null, 2));
            toast(t("common.copied"), "success");
          } catch (error) {
            toast(t("common.copyFailed"), "error");
          }
        },
      }, { label: t("common.buttons.close") }],
    });
  };

  const removeDialog = (item) => {
    const deleteFiles = h("input", { type: "checkbox", checked: true });
    const stopFirst = h("input", { type: "checkbox", checked: true });
    modal({
      title: t("catalog.remove.title", { name: item.name }),
      body: h("div", {},
        h("p", {}, t("catalog.remove.body")),
        h("label", { class: "check" }, stopFirst, t("catalog.remove.stopFirst")),
        h("div", {}, h("label", { class: "check" }, deleteFiles, t("catalog.remove.deleteFiles", { dir: item.dir }))),
        h("p", { class: "small muted" }, t("catalog.remove.volumesNote"))),
      width: 560,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.delete"), kind: "danger",
          onClick: async (close) => {
            const result = await guard(() => api.del(`/api/catalog/deployed/${encodeURIComponent(item.slug)}`, {
              delete_files: deleteFiles.checked, stop_first: stopFirst.checked,
            }), { quiet: true });
            if (!result || result.ok === false) {
              toast((result && result.error) || t("catalog.remove.failed"), "error");
              return;
            }
            toast(result.stopped ? t("catalog.remove.doneWith", { stopped: result.stopped }) : t("common.toast.deleted"), "success");
            close();
            loadDeployed();
          },
        },
      ],
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладки                                                            */
  /* ------------------------------------------------------------------ */

  const searchInput = input({ placeholder: t("catalog.search"), value: query });
  searchInput.addEventListener("input", () => {
    clearTimeout(searchInput._timer);
    searchInput._timer = setTimeout(() => { query = searchInput.value.trim(); loadRecipes(); }, 300);
  });

  const drawTab = () => {
    if (activeTab === "recipes") {
      mount(tabHost, toolbar(searchInput, h("span", { class: "spacer" }), button(t("common.buttons.refresh"), loadRecipes, { kind: "ghost", size: "sm" })));
      loadRecipes();
    } else {
      mount(bodyHost, deployedHost);
      mount(tabHost, toolbar(h("span", { class: "spacer" }), button(t("common.buttons.refresh"), loadDeployed, { kind: "ghost", size: "sm" })));
      loadDeployed();
    }
  };

  const tabBar = tabs(
    [
      { id: "recipes", label: t("catalog.tabs.recipes") },
      { id: "deployed", label: t("catalog.tabs.deployed") },
    ],
    { onChange: (id) => { activeTab = id; drawTab(); } }
  );

  mount(root, head, h("div", { class: "col" }, tabBar, tabHost, bodyHost));
  mount(head,
    h("div", {}, h("h1", { class: "view-title" }, t("catalog.title")),
      h("div", { class: "view-sub" }, t("catalog.subtitle"))),
    h("div", { class: "toolbar" }, button(t("nav.jobs.title"), () => (location.hash = "#/jobs"), { kind: "ghost" })));

  drawTab();

  cleanups.push(poller(15000, () => {
    if (document.hidden || activeTab !== "deployed") return;
    return loadDeployed();
  }, { immediate: false }));
  return () => cleanups.forEach((stop) => stop && stop());
}
