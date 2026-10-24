/* Редактор конфигов: дерево файлов в песочнице MegaTool, вкладки, проверка синтаксиса.
   Несохранённые правки хранятся черновиками в sessionStorage. */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, card, confirmDialog, empty, field, guard, h, input, modal, mount, notice,
  promptDialog, select, table, toast, toolbar,
} from "../ui.js";

/* --------------------------------------------------------------------- */
/*  Черновики редактора: ключ megatool.draft:<абсолютный путь>            */
/* --------------------------------------------------------------------- */

const draftKey = (path) => `megatool.draft:${path}`;

const readDraft = (path) => {
  try {
    const raw = sessionStorage.getItem(draftKey(path));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && typeof parsed.content === "string" ? parsed : null;
  } catch (error) {
    return null;
  }
};

const writeDraft = (path, content) => {
  try {
    sessionStorage.setItem(draftKey(path), JSON.stringify({ content, ts: Date.now() }));
  } catch (error) {
    /* переполнение sessionStorage — работаем без черновика */
  }
};

const removeDraft = (path) => {
  try {
    sessionStorage.removeItem(draftKey(path));
  } catch (error) {
    /* ignore */
  }
};

export async function render(root, { app = null } = {}) {
  const cleanups = [];
  let roots = [];
  let currentRoot = "";
  let openFiles = [];  // {path, absolute, title, text, original, kind, readonly, dirty, size_human, draft}
  let activePath = null;

  const treeHost = h("div");
  const tabsHost = h("div", { class: "file-tabs" });
  const editorHost = h("div");
  const draftHost = h("div");
  const validateHost = h("div");
  const searchResults = h("div");

  /* ------------------------------------------------------------------ */
  /*  Дерево                                                             */
  /* ------------------------------------------------------------------ */

  const loadTree = async () => {
    mount(treeHost, h("div", { class: "loading" }, "…"));
    const result = await guard(() => api.get("/api/files/tree", { root: currentRoot, depth: 4 }), { quiet: true });
    if (!result) return;
    if (result.ok === false) {
      mount(treeHost, notice(result.error || t("configs.treeFailed"), "bad"));
      return;
    }
    const renderNodes = (nodes, depth = 0) => nodes.map((node) => {
      if (node.type === "dir") {
        const children = h("div", { class: "tree-children" }, node.children && node.children.length ? renderNodes(node.children, depth + 1) : h("div", { class: "tree-node muted small" }, t("configs.emptyDir")));
        const row = h("div", { class: "tree-node", onclick: () => { children.hidden = !children.hidden; } },
          h("span", { class: "tree-mark" }, children.hidden ? "+" : "−"), h("span", {}, node.name));
        return h("div", {}, row, children);
      }
      return h("div", {
        class: `tree-node ${activePath === node.path ? "active" : ""}`,
        title: `${node.path} • ${node.size_human}`,
        onclick: () => openFile(node.path),
      }, h("span", { class: "tree-mark" }, "·"), h("span", { class: "mono", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, node.name));
    });
    mount(treeHost,
      result.truncated ? notice(t("configs.treeTruncated", { n: result.count }), "warn") : null,
      h("div", { class: "tree" }, renderNodes(result.nodes || [])));
  };

  const runSearch = async (text) => {
    if (!text || text.length < 2) {
      mount(searchResults);
      return;
    }
    const result = await guard(() => api.get("/api/files/search", { q: text, root: currentRoot, limit: 60 }), { quiet: true });
    if (!result) return;
    const matches = result.matches || [];
    mount(searchResults, matches.length
      ? h("div", {},
          h("div", { class: "small muted", style: { padding: "4px 8px" } }, t("configs.searchFound", { n: matches.length, scanned: result.scanned })),
          h("div", { class: "tree" }, matches.map((match) =>
            h("div", { class: "tree-node", title: match.text, onclick: () => openFile(match.path, match.line) },
              h("span", { class: "tree-mark" }, String(match.line)),
              h("span", { class: "mono small", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, `${match.path.split("/").pop()}: ${match.text.slice(0, 60)}`)))))
      : h("div", { class: "small muted", style: { padding: "4px 8px" } }, t("configs.searchEmpty")));
  };

  /* ------------------------------------------------------------------ */
  /*  Вкладки и редактор                                                 */
  /* ------------------------------------------------------------------ */

  const drawTabs = () => {
    mount(tabsHost, openFiles.map((file) =>
      h("div", {
        class: `file-tab ${file.path === activePath ? "active" : ""}`,
        onclick: () => { if (file.path !== activePath) { activePath = file.path; drawTabs(); drawEditor(); } },
      },
        h("span", { class: "mono" }, file.title),
        file.dirty ? h("span", { class: "dirty", title: t("configs.dirtyHint") }, "•") : null,
        h("span", {
          class: "close", title: t("common.buttons.close"),
          onclick: async (event) => {
            event.stopPropagation();
            if (file.dirty && !(await confirmDialog(t("configs.closeConfirm", { name: file.title })))) return;
            openFiles = openFiles.filter((item) => item !== file);
            if (activePath === file.path) activePath = openFiles.length ? openFiles[openFiles.length - 1].path : null;
            drawTabs();
            drawEditor();
          },
        }, "✕"))));
  };

  const openFile = async (path, line = null) => {
    if (!path) return;
    const existing = openFiles.find((file) => file.path === path);
    activePath = path;
    if (existing) {
      drawTabs();
      drawEditor(line);
      return;
    }
    const result = await guard(() => api.get("/api/files/read", { path }), { quiet: true });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("configs.openFailed"), "error");
      return;
    }
    const draft = readDraft(result.absolute || result.path);
    openFiles.push({
      path: result.path,
      absolute: result.absolute || result.path,
      title: result.path.split("/").pop(),
      text: result.text,
      original: result.text,
      kind: result.kind,
      readonly: result.readonly,
      size_human: result.size_human,
      dirty: false,
      // Панель показываем только если черновик отличается от содержимого на диске.
      draft: draft && draft.content !== result.text ? draft : null,
    });
    activePath = result.path;
    drawTabs();
    drawEditor(line);
    loadTree();
  };

  const currentFile = () => openFiles.find((file) => file.path === activePath) || null;

  /* Панель найденного черновика над редактором. */
  const drawDraftPanel = (file, editor) => {
    if (!file || !file.draft) {
      mount(draftHost);
      return;
    }
    mount(draftHost, h("div", { class: "notice notice-warn", style: { marginBottom: "8px" } },
      h("div", { class: "row row-wrap" },
        h("span", { class: "grow" }, t("configs.draftFound")),
        button(t("configs.draftRestore"), () => {
          editor.value = file.draft.content;
          file.text = file.draft.content;
          file.dirty = file.text !== file.original;
          file.draft = null;
          drawTabs();
          drawDraftPanel(file, editor);
          editor.focus();
        }, { size: "sm", kind: "primary" }),
        button(t("configs.draftRemove"), () => {
          removeDraft(file.absolute);
          file.draft = null;
          drawDraftPanel(file, editor);
        }, { size: "sm", kind: "ghost" }),
        button(t("configs.draftHide"), () => {
          file.draft = null;
          drawDraftPanel(file, editor);
        }, { size: "sm", kind: "ghost" }))));
  };

  const drawEditor = (line = null) => {
    const file = currentFile();
    if (!file) {
      mount(editorHost, empty(t("configs.noFile"), { mark: "✎", hint: t("configs.noFileHint") }));
      mount(draftHost);
      mount(validateHost);
      return;
    }
    const editor = h("textarea", { class: "code-editor", spellcheck: "false", readonly: file.readonly, value: file.text });
    editor.addEventListener("input", () => {
      file.text = editor.value;
      const dirty = file.text !== file.original;
      if (dirty) writeDraft(file.absolute, file.text);
      else removeDraft(file.absolute);  // вернулись к содержимому на диске — черновик не нужен
      if (dirty !== file.dirty) {
        file.dirty = dirty;
        drawTabs();
      }
    });
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Tab") {
        event.preventDefault();
        const start = editor.selectionStart;
        editor.setRangeText("  ", start, editor.selectionEnd, "end");
        file.text = editor.value;
        if (file.text !== file.original) writeDraft(file.absolute, file.text);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveFile(file, editor);
      }
    });
    if (line) {
      setTimeout(() => {
        const index = file.text.split("\n").slice(0, line - 1).join("\n").length;
        editor.focus();
        editor.setSelectionRange(index, index);
        const ratio = (line / Math.max(1, file.text.split("\n").length));
        editor.scrollTop = ratio * editor.scrollHeight;
      }, 60);
    }
    drawDraftPanel(file, editor);
    mount(editorHost,
      h("div", { class: "toolbar", style: { marginBottom: "8px" } },
        h("span", { class: "mono", style: { flex: "1", minWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, file.path),
        badge(file.kind, "muted"),
        file.readonly ? badge(t("configs.readonly"), "warn") : null,
        badge(file.size_human || "", "muted"),
        h("span", { class: "spacer" }),
        button(t("common.buttons.save"), () => saveFile(file), { kind: "primary", size: "sm", disabled: file.readonly }),
        button(t("configs.validate"), () => validateFile(file), { size: "sm", kind: "ghost" })),
      draftHost,
      editor);
    mount(validateHost);
  };

  const saveFile = async (file, editor) => {
    const result = await guard(() => api.post("/api/files/write", { path: file.path, content: file.text, backup: true }), { quiet: true });
    if (!result) return;
    if (result.ok === false) {
      toast(result.error || t("configs.saveFailed"), "error");
      return;
    }
    file.original = file.text;
    file.dirty = false;
    file.draft = null;
    removeDraft(file.absolute);  // содержимое записано на диск — черновик больше не нужен
    mount(draftHost);
    drawTabs();
    const validation = result.validation || {};
    const created = result.created ? t("configs.savedCreated") : "";
    const backup = result.backup ? t("configs.savedBackup", { path: result.backup }) : "";
    renderValidation(validation, t("configs.saved", { created, backup }));
    toast(t("configs.fileSaved"), "success");
    if (editor) editor.focus();
  };

  const validateFile = async (file) => {
    const result = await guard(() => api.post("/api/files/validate", {
      filename: file.path.split("/").pop(),
      content: file.text,
      path: file.path,
      nginx_test: file.kind === "nginx",
    }), { quiet: true });
    if (!result) return;
    const data = result.data || {};
    renderValidation(data, t("configs.syntaxCheck"));
  };

  const renderValidation = (validation, header) => {
    const errors = validation.errors || [];
    const warnings = validation.warnings || [];
    mount(validateHost,
      h("div", { class: "card-body pad-sm" },
        h("div", { class: "row row-wrap" },
          badge(validation.ok ? t("configs.syntaxOk") : t("configs.syntaxErrors"), validation.ok ? "ok" : "bad"),
          validation.kind ? badge(validation.kind, "muted") : null,
          h("span", { class: "muted small" }, header || ""),
          validation.nginx ? badge(`nginx -t: ${validation.nginx.ok ? t("configs.nginxOk") : t("configs.nginxFail")}`, validation.nginx.ok ? "ok" : "bad") : null),
        errors.length ? h("ul", { class: "validation-list" }, errors.map((text) => h("li", {}, `✕ ${text}`))) : null,
        warnings.length ? h("ul", { class: "validation-list" }, warnings.map((text) => h("li", {}, `! ${text}`))) : null,
        validation.nginx && !validation.nginx.ok ? h("pre", { class: "mono-block" }, validation.nginx.text || validation.nginx.error || "") : null,
        validation.info && Object.keys(validation.info).length ? h("pre", { class: "mono-block" }, JSON.stringify(validation.info, null, 2)) : null));
  };

  /* ------------------------------------------------------------------ */
  /*  Операции с файлами                                                 */
  /* ------------------------------------------------------------------ */

  const newFromTemplate = async () => {
    const result = await guard(() => api.get("/api/templates"), { quiet: true });
    if (!result) return;
    const items = result.items || [];
    const templateSelect = select(items.map((item) => ({ value: item.id, label: `${item.name} — ${item.filename}` })));
    const pathInput = input({ value: (currentRoot ? "" : "configs/") });
    const hints = h("p", { class: "small muted" }, "");
    const updateHint = async () => {
      const full = await guard(() => api.get(`/api/templates/${encodeURIComponent(templateSelect.value)}`), { quiet: true });
      mount(hints, full && full.data ? full.data.description : "");
    };
    templateSelect.addEventListener("change", updateHint);
    updateHint();
    modal({
      title: t("configs.newTemplateTitle"),
      body: h("div", {},
        field(t("configs.templateLabel"), templateSelect),
        field(t("configs.pathLabel"), pathInput, t("configs.pathHint")),
        hints),
      width: 640,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.create"), kind: "primary",
          onClick: async (close) => {
            const full = await guard(() => api.get(`/api/templates/${encodeURIComponent(templateSelect.value)}`), { quiet: true });
            if (!full || !full.data) return;
            const path = pathInput.value.trim() || full.data.filename;
            const written = await guard(() => api.post("/api/files/write", { path, content: full.data.content, backup: false }), { quiet: true });
            if (!written || written.ok === false) {
              toast((written && written.error) || t("configs.createFailed"), "error");
              return;
            }
            close();
            toast(t("configs.fileCreated"), "success");
            await loadTree();
            openFile(written.path || path);
          },
        },
      ],
    });
  };

  const mkdirDialog = async () => {
    const path = await promptDialog(t("configs.mkdirPrompt"), { title: t("configs.mkdirTitle"), value: t("configs.mkdirDefault") });
    if (!path) return;
    const result = await guard(() => api.post("/api/files/mkdir", { path }), { quiet: true });
    if (result && result.ok) {
      toast(t("configs.dirCreated"), "success");
      loadTree();
    } else if (result) {
      toast(result.error || t("configs.dirCreateFailed"), "error");
    }
  };

  // Абсолютный путь для ключа черновика: сервер отдаёт его при чтении файла,
  // а после переименования вычисляем от корня рабочего каталога.
  const toAbsolute = (path) => {
    if (/^([A-Za-z]:[\\/]|\\\\|\/)/.test(path)) return path;
    const home = (rootsResult && rootsResult.home) || "";
    return home ? `${home.replace(/[\\/]+$/, "")}/${path}` : path;
  };

  const renameDialog = async () => {
    const file = currentFile();
    if (!file) { toast(t("configs.openFirst"), "warn"); return; }
    const newPath = await promptDialog(t("configs.renamePrompt"), { title: t("common.buttons.rename"), value: file.path });
    if (!newPath || newPath === file.path) return;
    const result = await guard(() => api.post("/api/files/rename", { path: file.path, new_path: newPath }), { quiet: true });
    if (result && result.ok) {
      const newAbsolute = toAbsolute(result.path);
      const draft = readDraft(file.absolute);
      if (draft && newAbsolute !== file.absolute) {
        removeDraft(file.absolute);
        writeDraft(newAbsolute, draft.content);  // черновик переезжает вместе с файлом
      }
      file.path = result.path;
      file.absolute = newAbsolute;
      file.title = result.path.split("/").pop();
      activePath = result.path;
      toast(t("configs.renamed"), "success");
      drawTabs();
      drawEditor();
      loadTree();
    } else if (result) {
      toast(result.error || t("configs.renameFailed"), "error");
    }
  };

  const deleteFile = async () => {
    const file = currentFile();
    if (!file) { toast(t("configs.openFirst"), "warn"); return; }
    if (!(await confirmDialog(t("configs.deleteConfirm", { path: file.path })))) return;
    const result = await guard(() => api.post("/api/files/delete", { path: file.path, recursive: false }), { quiet: true });
    if (result && result.ok) {
      removeDraft(file.absolute);  // файла больше нет — черновик не нужен
      openFiles = openFiles.filter((item) => item !== file);
      activePath = openFiles.length ? openFiles[openFiles.length - 1].path : null;
      toast(t("common.toast.deleted"), "success");
      drawTabs();
      drawEditor();
      loadTree();
    } else if (result) {
      toast(result.error || t("configs.deleteFailed"), "error");
    }
  };

  const uploadDialog = () => {
    const chooser = h("input", { type: "file" });
    const pathInput = input({ value: currentRoot || "" });
    modal({
      title: t("configs.uploadTitle"),
      body: h("div", {}, field(t("configs.uploadDir"), pathInput), field(t("configs.uploadFile"), chooser, t("configs.uploadLimit"))),
      width: 560,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.upload"), kind: "primary",
          onClick: async (close) => {
            const file = chooser.files && chooser.files[0];
            if (!file) { toast(t("configs.chooseFile"), "warn"); return; }
            const reader = new FileReader();
            reader.onload = async () => {
              const base64 = String(reader.result).split(",", 2)[1] || "";
              const result = await guard(() => api.post("/api/files/upload", { path: pathInput.value.trim() || currentRoot, filename: file.name, content_base64: base64 }), { quiet: true });
              if (result && result.ok) {
                toast(t("configs.uploaded", { path: result.path }), "success");
                close();
                loadTree();
              } else if (result) {
                toast(result.error || t("configs.uploadFailed"), "error");
              }
            };
            reader.readAsDataURL(file);
          },
        },
      ],
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Уход из раздела                                                    */
  /* ------------------------------------------------------------------ */

  // Есть ли у открытого файла черновик, отличающийся от содержимого при открытии.
  const hasUnsavedDraft = () => {
    const file = currentFile();
    if (!file) return false;
    const draft = readDraft(file.absolute);
    return Boolean(draft) && draft.content !== file.original;
  };

  // Синхронный хук роутера: при отказе пользователя переход отменяется.
  let leaveAllowed = false;
  const beforeNavigate = () => {
    if (leaveAllowed) return true;
    if (!hasUnsavedDraft()) return true;
    const target = location.hash;  // куда уходим: к моменту вызова хэш уже изменён
    confirmDialog(t("configs.draftLeaveConfirm"), { okLabel: t("configs.draftLeaveOk") }).then((ok) => {
      if (!ok) return;
      leaveAllowed = true;  // пользователь разрешил уход — повторный хук не спрашиваем
      if (app) app.beforeNavigate = null;
      if (location.hash === target) window.dispatchEvent(new Event("hashchange"));
      else location.hash = target;  // повторяем отменённый переход
    });
    return false;
  };

  /* ------------------------------------------------------------------ */
  /*  Сборка вида                                                        */
  /* ------------------------------------------------------------------ */

  const rootsResult = await guard(() => api.get("/api/files/roots"), { quiet: true });
  roots = (rootsResult && rootsResult.items) || [];

  const pending = sessionStorage.getItem("megatool.openPath");
  sessionStorage.removeItem("megatool.openPath");
  // Путь приходит из каталога приложений и Ansible: файл открываем в редакторе,
  // каталог — просто показываем в дереве (глубина 4 уровня покрывает apps/<slug>).
  const pendingIsFile = Boolean(pending) && /[\\/.][A-Za-z0-9_-]{1,20}$/.test(pending) && !/[\\/]$/.test(pending);

  const rootSelect = select(roots.map((root) => ({ value: root.path, label: `${root.label} — ${root.path}` })), { value: pending ? "" : "" });
  rootSelect.addEventListener("change", () => {
    currentRoot = rootSelect.value;
    mount(searchResults);
    loadTree();
  });

  const searchInput = input({ placeholder: t("configs.searchPlaceholder") });
  searchInput.addEventListener("input", () => {
    clearTimeout(searchInput._timer);
    searchInput._timer = setTimeout(() => runSearch(searchInput.value.trim()), 350);
  });

  mount(root,
    h("div", { class: "view-head" },
      h("div", {}, h("h1", { class: "view-title" }, t("configs.title")),
        h("div", { class: "view-sub" }, t("configs.subtitle", { home: rootsResult ? rootsResult.home : "" }))),
      h("div", { class: "toolbar" },
        button(t("configs.fromTemplate"), newFromTemplate, { kind: "primary" }),
        button(t("configs.mkdir"), mkdirDialog, { kind: "ghost" }),
        button(t("common.buttons.upload"), uploadDialog, { kind: "ghost" }),
        button(t("common.buttons.rename"), renameDialog, { kind: "ghost" }),
        button(t("common.buttons.delete"), deleteFile, { kind: "ghost" }))),
    h("div", { class: "editor-layout" },
      card(t("configs.filesCard"), h("div", {},
        h("div", { class: "col", style: { gap: "6px", marginBottom: "8px" } }, rootSelect, searchInput),
        treeHost, searchResults), { class: "card" }),
      card(t("configs.editorCard"), h("div", {}, tabsHost, editorHost, validateHost), { class: "card" })));

  currentRoot = "";
  await loadTree();
  if (pending && pendingIsFile) {
    try {
      const info = await api.get("/api/files/read", { path: pending });
      if (info.ok) openFile(info.path);
    } catch (error) {
      /* путь недоступен — остаёмся в дереве каталогов */
    }
  }
  drawTabs();
  drawEditor();

  if (app) {
    app.beforeNavigate = beforeNavigate;
    // Роутер (и переключатель языка) спрашивает, есть ли реально черновик.
    app.hasUnsavedDraft = hasUnsavedDraft;
  }

  cleanups.push(() => {
    if (searchInput._timer) clearTimeout(searchInput._timer);
    if (app && app.beforeNavigate === beforeNavigate) app.beforeNavigate = null;
    if (app && app.hasUnsavedDraft === hasUnsavedDraft) app.hasUnsavedDraft = null;
  });
  return () => cleanups.forEach((stop) => stop && stop());
}
