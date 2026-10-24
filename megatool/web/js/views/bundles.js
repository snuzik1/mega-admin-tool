/* Наборы конфигов: список наборов, файлы, редактор и экспорт в ZIP.

   Слева — список наборов, справа — метаданные выбранного набора, таблица
   файлов и редактор. Переключение наборов и файлов защищено от гонок:
   запоздавший ответ не должен перезаписывать уже выбранный набор. */

import { api } from "../api.js";
import { getLang, t } from "../i18n.js";
import {
  badge, button, card, confirmDialog, empty, field, fmtBytes, fmtTime, guard, h, input,
  modal, mount, promptDialog, select, spinner, table, timeAgo, toast, toolbar,
} from "../ui.js";

/** Заголовок шаблона на текущем языке интерфейса. */
function presetTitle(preset) {
  return getLang() === "en" ? (preset.title_en || preset.title) : preset.title;
}

/** Описание шаблона на текущем языке интерфейса. */
function presetDescription(preset) {
  return getLang() === "en" ? (preset.description_en || preset.description) : preset.description;
}

export async function render(root, { args = [], app = null, desc = "" } = {}) {
  let bundles = [];
  let presets = [];
  let currentId = (args && args[0]) || "";
  let current = null;
  let activePath = null;
  let savedText = "";        // содержимое, полученное с сервера
  let dirty = false;
  let generation = 0;        // защита от гонок при переключении наборов
  let fileGeneration = 0;    // то же для файлов внутри набора

  const head = h("div", { class: "view-head" });
  const listHost = h("div");
  const detailHost = h("div");
  const editorHost = h("div");

  /* ------------------------------------------------------------------ */
  /*  Загрузка данных                                                    */
  /* ------------------------------------------------------------------ */

  const loadList = async () => {
    const result = await guard(() => api.get("/api/bundles"), { quiet: true });
    bundles = (result && result.items) || [];
    if (currentId && !bundles.some((item) => item.id === currentId)) {
      currentId = "";
      current = null;
      activePath = null;
      savedText = "";
      dirty = false;
    }
    return bundles;
  };

  const loadPresets = async () => {
    if (presets.length) return presets;
    const result = await guard(() => api.get("/api/bundles/presets"), { quiet: true });
    presets = (result && result.items) || [];
    return presets;
  };

  const loadBundle = async (id) => {
    const mine = (generation += 1);
    const result = await guard(
      () => api.get(`/api/bundles/${encodeURIComponent(id)}`),
      { quiet: true },
    );
    if (mine !== generation) return null;  // ответ устарел: набор уже переключили
    if (!result || result.ok === false) {
      current = null;
      toast((result && result.error) || t("bundles.loadFailed"), "error");
      return null;
    }
    current = result;
    return current;
  };

  const confirmDiscard = () => (dirty
    ? confirmDialog(t("bundles.confirmDiscard"), {
        title: t("bundles.discardTitle"),
        okLabel: t("bundles.discardOk"),
        danger: false,
      })
    : Promise.resolve(true));

  /* ------------------------------------------------------------------ */
  /*  Список наборов                                                     */
  /* ------------------------------------------------------------------ */

  const drawList = () => {
    if (!bundles.length) {
      mount(listHost, empty(t("bundles.noBundles"), {
        mark: "▦",
        hint: t("bundles.noBundlesHint"),
        extra: button(t("bundles.create"), createDialog, { kind: "primary", size: "sm" }),
      }));
      return;
    }
    mount(listHost, table({
      columns: [
        {
          key: "name",
          label: t("bundles.columns.name"),
          render: (row) => h("div", {},
            h("div", { style: row.id === currentId ? { fontWeight: "600" } : null }, row.name),
            h("div", { class: "small muted mono" }, row.id)),
        },
        { key: "file_count", label: t("bundles.columns.files"), align: "right" },
        {
          key: "updated",
          label: t("bundles.columns.updated"),
          render: (row) => h("span", { title: fmtTime(row.updated) }, timeAgo(row.updated)),
        },
      ],
      rows: bundles,
      empty: t("bundles.noBundles"),
      onRow: (row) => openBundle(row.id),
    }));
  };

  /* ------------------------------------------------------------------ */
  /*  Выбранный набор                                                    */
  /* ------------------------------------------------------------------ */

  const drawDetail = () => {
    if (!current) {
      mount(detailHost, empty(t("bundles.selectBundle"), {
        mark: "▤",
        hint: t("bundles.selectBundleHint"),
      }));
      return;
    }
    const files = current.files || [];
    const filesTable = table({
      columns: [
        {
          key: "path",
          label: t("bundles.columns.path"),
          render: (row) => h("span", {
            class: "mono",
            style: row.path === activePath ? { fontWeight: "600" } : null,
          }, row.path),
        },
        {
          key: "size",
          label: t("bundles.columns.size"),
          align: "right",
          render: (row) => fmtBytes(row.size),
        },
        {
          key: "modified",
          label: t("bundles.columns.modified"),
          render: (row) => h("span", { title: fmtTime(row.modified) }, timeAgo(row.modified)),
        },
        {
          key: "actions",
          label: "",
          sortable: false,
          render: (row) => h("div", { class: "toolbar" },
            button(t("common.buttons.open"), () => openFile(row.path), { size: "sm", kind: "ghost" }),
            button(t("common.buttons.delete"), () => deleteFile(row.path), { size: "sm", kind: "danger" })),
        },
      ],
      rows: files,
      empty: t("bundles.noFiles"),
      onRow: (row) => openFile(row.path),
    });

    mount(detailHost, card(current.name, h("div", {},
      current.description ? h("p", { class: "muted" }, current.description) : null,
      h("div", { class: "chips" },
        h("span", { class: "tag" }, t("bundles.chipFiles", { n: files.length })),
        h("span", { class: "tag" }, t("bundles.chipSize", { size: fmtBytes(current.size) })),
        current.preset ? h("span", { class: "tag" }, t("bundles.chipPreset", { preset: current.preset })) : null,
        h("span", { class: "tag" }, t("bundles.chipUpdated", { ago: timeAgo(current.updated) }))),
      toolbar(
        button(t("bundles.downloadZip"), () => downloadZip(current), { kind: "primary", size: "sm" }),
        button(t("common.buttons.rename"), () => renameDialog(current), { size: "sm", kind: "ghost" }),
        button(t("common.buttons.delete"), () => deleteBundle(current), { size: "sm", kind: "danger" }),
      ),
      h("p", { class: "small muted", style: { margin: "6px 0 10px" } }, t("bundles.zipHint")),
      filesTable,
    ), {
      extra: toolbar(button(t("bundles.addFile"), () => addFileDialog(current), { size: "sm", kind: "ghost" })),
    }));
  };

  /* ------------------------------------------------------------------ */
  /*  Редактор файла                                                     */
  /* ------------------------------------------------------------------ */

  const drawEditor = () => {
    if (!current || !activePath) {
      mount(editorHost, empty(t("bundles.editorEmpty"), { hint: t("bundles.editorEmptyHint") }));
      return;
    }
    const file = (current.files || []).find((item) => item.path === activePath);
    if (!file) {
      activePath = null;
      savedText = "";
      dirty = false;
      mount(editorHost, empty(t("bundles.editorEmpty"), { hint: t("bundles.editorEmptyHint") }));
      return;
    }

    const editor = h("textarea", { class: "code-editor", spellcheck: "false" });
    editor.value = savedText;
    const status = badge(dirty ? t("bundles.modified") : t("bundles.saved"), dirty ? "warn" : "muted");
    const saveButton = button(t("common.buttons.save"), () => saveFile(editor.value), {
      kind: "primary", size: "sm", disabled: !dirty,
    });
    const revertButton = button(t("bundles.revert"), () => openFile(activePath, { force: true }), {
      size: "sm", kind: "ghost", disabled: !dirty,
    });

    const syncState = () => {
      dirty = editor.value !== savedText;
      status.textContent = dirty ? t("bundles.modified") : t("bundles.saved");
      status.className = `badge badge-${dirty ? "warn" : "muted"}`;
      saveButton.disabled = !dirty;
      revertButton.disabled = !dirty;
    };
    editor.addEventListener("input", syncState);
    editor.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty) saveFile(editor.value);
      }
    });

    mount(editorHost, card(t("bundles.editorTitle", { path: activePath }), h("div", {},
      toolbar(
        status,
        h("span", { class: "small muted" }, `${fmtBytes(file.size)} · ${fmtTime(file.modified)}`),
        saveButton,
        revertButton,
        button(t("bundles.closeFile"), closeEditor, { size: "sm", kind: "ghost" }),
      ),
      editor,
      h("p", { class: "small muted", style: { marginTop: "8px" } }, t("bundles.editorHint")),
    )));
  };

  /* ------------------------------------------------------------------ */
  /*  Действия                                                           */
  /* ------------------------------------------------------------------ */

  const openBundle = async (id) => {
    if (!(await confirmDiscard())) return;
    currentId = id;
    current = null;
    activePath = null;
    savedText = "";
    dirty = false;
    fileGeneration += 1;
    drawDetail();
    drawEditor();
    mount(detailHost, spinner(t("common.loading")));
    const loaded = await loadBundle(id);
    if (!loaded && currentId === id) currentId = "";
    drawList();
    drawDetail();
    drawEditor();
  };

  const openFile = async (path, { force = false } = {}) => {
    if (!current || !currentId) return;
    if (!force && path === activePath && !dirty) return;
    if (!(await confirmDiscard())) return;
    const mine = (fileGeneration += 1);
    const bundleId = currentId;
    const result = await guard(
      () => api.get(`/api/bundles/${encodeURIComponent(bundleId)}/file`, { path }),
      { quiet: true },
    );
    if (mine !== fileGeneration || bundleId !== currentId) return;  // устаревший ответ
    if (!result || result.ok === false) {
      toast((result && result.error) || t("bundles.fileLoadFailed"), "error");
      return;
    }
    activePath = result.path || path;
    savedText = result.content || "";
    dirty = false;
    drawDetail();
    drawEditor();
  };

  const closeEditor = async () => {
    if (!(await confirmDiscard())) return;
    fileGeneration += 1;
    activePath = null;
    savedText = "";
    dirty = false;
    drawDetail();
    drawEditor();
  };

  const refreshBundle = async ({ keepFile = false } = {}) => {
    await loadList();
    if (currentId) {
      await loadBundle(currentId);
    } else {
      current = null;
    }
    if (!keepFile) {
      fileGeneration += 1;
      activePath = null;
      savedText = "";
      dirty = false;
    }
    drawList();
    drawDetail();
    drawEditor();
  };

  const refreshAll = async () => {
    if (!(await confirmDiscard())) return;
    await loadList();
    if (currentId) await loadBundle(currentId);
    drawList();
    drawDetail();
    drawEditor();
  };

  const saveFile = async (content) => {
    if (!current || !currentId || !activePath) return;
    const bundleId = currentId;
    const path = activePath;
    const mine = fileGeneration;
    const result = await guard(
      () => api.post(`/api/bundles/${encodeURIComponent(bundleId)}/file`, { path, content }),
      { success: t("bundles.toastSaved") },
    );
    if (!result || result.ok === false) return;
    if (mine !== fileGeneration || bundleId !== currentId || path !== activePath) return;
    savedText = content;
    dirty = false;
    await refreshBundle({ keepFile: true });
  };

  const addFileDialog = async (bundle) => {
    const value = await promptDialog(t("bundles.addFilePrompt"), {
      title: t("bundles.addFileTitle"),
      placeholder: t("bundles.addFilePlaceholder"),
    });
    if (value === null) return;
    const path = String(value).trim();
    if (!path) return;
    const result = await guard(
      () => api.post(`/api/bundles/${encodeURIComponent(bundle.id)}/files`, { path }),
      { success: t("bundles.toastFileAdded") },
    );
    if (!result || result.ok === false) return;
    await refreshBundle();
    await openFile(result.path || path);
  };

  const deleteFile = async (path) => {
    if (!currentId) return;
    const ok = await confirmDialog(t("bundles.confirmDeleteFile", { path }), {
      title: t("bundles.deleteFileTitle"),
      okLabel: t("common.buttons.delete"),
    });
    if (!ok) return;
    const result = await guard(
      () => api.del(`/api/bundles/${encodeURIComponent(currentId)}/file`, { path }),
      { success: t("bundles.toastFileDeleted") },
    );
    if (!result || result.ok === false) return;
    await refreshBundle();
  };

  const renameDialog = async (bundle) => {
    const value = await promptDialog(t("bundles.renamePrompt"), {
      title: t("bundles.renameTitle"),
      value: bundle.name,
    });
    if (value === null) return;
    const name = String(value).trim();
    if (!name || name === bundle.name) return;
    const result = await guard(
      () => api.patch(`/api/bundles/${encodeURIComponent(bundle.id)}`, { name }),
      { success: t("bundles.toastRenamed") },
    );
    if (!result || result.ok === false) return;
    await refreshBundle({ keepFile: true });
  };

  const deleteBundle = async (bundle) => {
    const ok = await confirmDialog(t("bundles.confirmDelete", { name: bundle.name }), {
      title: t("bundles.deleteTitle"),
      okLabel: t("common.buttons.delete"),
    });
    if (!ok) return;
    const result = await guard(
      () => api.del(`/api/bundles/${encodeURIComponent(bundle.id)}`),
      { success: t("bundles.toastDeleted") },
    );
    if (!result || result.ok === false) return;
    generation += 1;
    fileGeneration += 1;
    if (currentId === bundle.id) {
      currentId = "";
      current = null;
      activePath = null;
      savedText = "";
      dirty = false;
    }
    await loadList();
    drawList();
    drawDetail();
    drawEditor();
  };

  const downloadZip = async (bundle) => {
    // Скачиваем через fetch: ссылка создаётся уже на готовый Blob.
    const url = `/api/bundles/${encodeURIComponent(bundle.id)}/export`;
    try {
      const response = await fetch(url);
      if (!response.ok) {
        let message = t("common.toast.requestFailed");
        try {
          const data = await response.json();
          if (data && data.error) message = data.error;
        } catch (error) {
          /* не JSON — оставляем общий текст */
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const link = h("a", { href: URL.createObjectURL(blob), download: `${bundle.id}.zip` });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    } catch (error) {
      toast(error.message || t("common.toast.requestFailed"), "bad");
    }
  };

  const createDialog = async () => {
    await loadPresets();
    const nameInput = input({ placeholder: t("bundles.namePlaceholder") });
    const descriptionInput = input({ placeholder: t("bundles.descriptionPlaceholder") });
    const presetSelect = select(
      [{ value: "", label: t("bundles.presetNone") }].concat(
        presets.map((preset) => ({ value: preset.id, label: presetTitle(preset) })),
      ),
      { value: "" },
    );
    const presetHint = h("span", { class: "field-hint" }, t("bundles.presetHint"));
    presetSelect.addEventListener("change", () => {
      const preset = presets.find((item) => item.id === presetSelect.value);
      presetHint.textContent = preset
        ? `${presetDescription(preset)} (${t("bundles.presetFiles", { n: (preset.files || []).length })})`
        : t("bundles.presetHint");
    });

    modal({
      title: t("bundles.createTitle"),
      width: 560,
      body: h("div", {},
        field(t("bundles.name"), nameInput),
        field(t("bundles.description"), descriptionInput),
        field(t("bundles.preset"), presetSelect, presetHint)),
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.create"),
          kind: "primary",
          onClick: async (close) => {
            const name = nameInput.value.trim();
            if (!name) {
              toast(t("bundles.needName"), "warn");
              return;
            }
            const result = await guard(
              () => api.post("/api/bundles", {
                name,
                description: descriptionInput.value.trim(),
                preset: presetSelect.value,
              }),
              { success: t("bundles.toastCreated") },
            );
            if (!result || result.ok === false) return;
            close();
            await loadList();
            await openBundle(result.id);
          },
        },
      ],
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Старт                                                              */
  /* ------------------------------------------------------------------ */

  mount(root,
    head,
    h("div", { class: "editor-layout" },
      h("div", { class: "col" }, listHost),
      h("div", { class: "col" }, detailHost, editorHost)));

  mount(head,
    h("div", {},
      h("h1", { class: "view-title" }, t("bundles.title")),
      h("div", { class: "view-sub" }, desc || t("bundles.subtitle"))),
    toolbar(
      button(t("bundles.create"), createDialog, { kind: "primary", size: "sm" }),
      button(t("common.buttons.refresh"), refreshAll, { size: "sm", kind: "ghost" }),
    ));

  await loadList();
  drawList();
  if (currentId && bundles.some((item) => item.id === currentId)) {
    await loadBundle(currentId);
  } else {
    currentId = "";
  }
  drawDetail();
  drawEditor();

  // Очистка: запоздавшие ответы после ухода из раздела игнорируются.
  return () => {
    generation += 1;
    fileGeneration += 1;
  };
}
