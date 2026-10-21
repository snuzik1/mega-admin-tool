/* Доступ: пользователи, пресеты прав и вход по логину/паролю.
   Пресеты задают матрицу «раздел → просмотр/управление». */

import { api } from "../api.js";
import { t } from "../i18n.js";
import {
  badge, button, can, confirmDialog, empty, field, fmtNumber, guard, h, input,
  modal, mount, notice, openSetupWizard, select, table, tabs, toast,
} from "../ui.js";

const SECTIONS = [
  ["dashboard", "access.section.dashboard"],
  ["checks", "access.section.checks"],
  ["hosts", "access.section.hosts"],
  ["containers", "access.section.containers"],
  ["balancer", "access.section.balancer"],
  ["catalog", "access.section.catalog"],
  ["bundles", "access.section.bundles"],
  ["configs", "access.section.configs"],
  ["ansible", "access.section.ansible"],
  ["servers", "access.section.servers"],
  ["k8s", "access.section.k8s"],
  ["changes", "access.section.changes"],
  ["jobs", "access.section.jobs"],
  ["settings", "access.section.settings"],
  ["access", "access.section.access"],
];

const LEVELS = [
  { value: "none", key: "access.level.none" },
  { value: "view", key: "access.level.view" },
  { value: "manage", key: "access.level.manage" },
];

const presetName = (data, id) => {
  const preset = (data.presets || []).find((item) => item.id === id);
  return preset ? preset.name : t("common.dash");
};

const userOf = (user) => ({
  id: user.id || "",
  username: user.username,
  display_name: user.display_name || "",
  preset_id: user.preset_id,
  enabled: user.enabled !== false,
  owner: user.owner === true,
  local_account: user.local_account || "",
});

const elevatedPreset = (preset) => Boolean(preset
  && ["access", "settings"].some((section) => (preset.permissions || {})[section] === "manage"));

export async function render(root) {
  let data = { presets: [], users: [], auth_enabled: false, local_only: false, sessions: 0 };
  let activeTab = "users";
  const headHost = h("div");
  const tabsHost = h("div");
  const bodyHost = h("div");

  const saveUsers = async (users) => {
    const result = await guard(() => api.put("/api/access/users", { users }), {
      success: t("access.users.saved"),
    });
    if (!result) return false;
    if (result.ok === false) {
      toast(result.error || t("access.users.saveFailed"), "error");
      return false;
    }
    data = result.data || data;
    draw();
    return true;
  };

  const savePresets = async (presets) => {
    const result = await guard(() => api.put("/api/access/presets", { presets }), {
      success: t("access.presets.saved"),
    });
    if (!result) return false;
    if (result.ok === false) {
      toast(result.error || t("access.presets.saveFailed"), "error");
      return false;
    }
    data = result.data || data;
    draw();
    return true;
  };

  const load = async () => {
    const result = await guard(() => api.get("/api/access"), { quiet: true });
    if (!result || result.ok === false) return;
    data = result.data || data;
    draw();
  };

  /* ------------------------------------------------------------------ */
  /*  Пользователи                                                       */
  /* ------------------------------------------------------------------ */

  const canManagePrivileged = () => {
    const access = window.megatoolAccess || {};
    return access.mode === "master" || access.mode === "local"
      || Boolean(access.user && access.user.owner);
  };
  const presetById = (id) => (data.presets || []).find((item) => item.id === id);
  const protectedUser = (user) => Boolean(user.owner) || elevatedPreset(presetById(user.preset_id));
  const canTouchUser = (user) => canManagePrivileged() || !protectedUser(user);

  const editUser = (user = null) => {
    if (!(data.presets || []).length) {
      toast(t("access.users.needPreset"), "warn");
      return;
    }
    const source = user ? userOf(user) : {
      username: "", display_name: "", preset_id: data.presets[0].id, enabled: true,
      owner: false, local_account: "",
    };
    const privilegedEditor = canManagePrivileged();
    const usernameInput = input({
      value: source.username || "", maxlength: "32", placeholder: "operator",
      disabled: Boolean(user),
    });
    const displayInput = input({ value: source.display_name || "", maxlength: "80", placeholder: t("access.users.displayPlaceholder") });
    const localInput = input({
      value: source.local_account || "", maxlength: "64",
      placeholder: t("access.users.localPlaceholder"),
    });
    const passwordInput = user ? null : input({
      type: "password", value: "", maxlength: "200",
      placeholder: t("access.users.passwordPlaceholder"),
    });
    const availablePresets = data.presets.filter((preset) =>
      privilegedEditor || !elevatedPreset(preset) || preset.id === source.preset_id);
    const presetSelect = select(
      availablePresets.map((preset) => ({ value: preset.id, label: preset.name })),
      { value: source.preset_id },
    );
    const enabledInput = h("input", { type: "checkbox", checked: source.enabled !== false });
    const ownerInput = privilegedEditor
      ? h("input", { type: "checkbox", checked: source.owner === true })
      : null;

    const body = h("div", { class: "col" },
      h("div", { class: "grid cards-2" },
        field(t("access.users.username"), usernameInput, t("access.users.usernameHint")),
        field(t("access.users.displayName"), displayInput, t("access.users.displayHint"))),
      h("div", { class: "grid cards-2" },
        field(t("access.users.preset"), presetSelect, t("access.users.presetHint")),
        h("label", { class: "check" }, enabledInput, h("span", {}, t("access.users.enabled")))),
      field(t("access.users.localAccount"), localInput,
        data.local_only ? t("access.users.localHint") : `${t("access.users.localHint")} ${t("access.users.localOptional")}`),
      ownerInput
        ? h("div", { class: "col" },
            h("label", { class: "check" }, ownerInput, h("span", {}, t("access.users.owner"))),
            h("p", { class: "small muted" }, t("access.users.ownerHint")))
        : null,
      user
        ? h("p", { class: "small muted" }, t("access.users.passwordSelfHint"))
        : field(t("access.users.password"), passwordInput, t("access.users.passwordHint")));

    modal({
      title: user ? t("access.users.editTitle") : t("access.users.addTitle"),
      width: 620,
      body,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.save"),
          kind: "primary",
          onClick: async (close) => {
            const username = usernameInput.value.trim().toLowerCase();
            const password = passwordInput ? passwordInput.value : "";
            const localAccount = localInput.value.trim();
            if (!username || (!user && !password)) {
              toast(!username ? t("access.users.usernameRequired") : t("access.users.passwordRequired"), "warn");
              return false;
            }
            if (data.local_only && !localAccount) {
              toast(t("access.users.localRequired"), "warn");
              return false;
            }
            const entry = {
              id: source.id || "",
              username,
              display_name: displayInput.value.trim(),
              preset_id: presetSelect.value,
              enabled: enabledInput.checked,
              owner: ownerInput ? ownerInput.checked : source.owner === true,
              local_account: localAccount,
            };
            if (password) entry.password = password;
            const users = data.users.map(userOf);
            const index = users.findIndex((item) => item.id && item.id === entry.id);
            if (index === -1) users.push(entry);
            else users[index] = entry;
            if (await saveUsers(users)) close();
            return false;
          },
        },
      ],
    });
  };

  const toggleUser = async (user) => {
    const users = data.users.map(userOf);
    const target = users.find((item) => item.id === user.id);
    if (!target) return;
    target.enabled = target.enabled === false;
    await saveUsers(users);
  };

  const removeUser = async (user) => {
    const ok = await confirmDialog(t("access.users.deleteConfirm", { name: user.username }), {
      title: t("access.users.deleteTitle"), okLabel: t("common.buttons.delete"),
    });
    if (!ok) return;
    await saveUsers(data.users.filter((item) => item.id !== user.id).map(userOf));
  };

  const drawUsers = () => {
    const host = h("div", { class: "col" });
    if (!data.users.length) {
      const addButton = button(t("access.users.add"), () => editUser(), { kind: "primary", size: "sm" });
      const setupButton = !data.auth_enabled
        ? button(t("common.setup.create"), () => openSetupWizard(), { kind: "primary", size: "sm" })
        : null;
      mount(host, empty(t("access.users.empty"), {
        mark: "⚿",
        hint: t("access.users.emptyHint"),
        extra: h("div", { class: "row row-wrap" }, setupButton, addButton),
      }));
      return host;
    }
    mount(host, table({
      columns: [
        {
          key: "username", label: t("access.users.username"),
          render: (row) => {
            const role = row.local_role === "root"
              ? t("access.users.localRoot")
              : row.local_role === "administrator"
                ? t("access.users.localAdmin")
                : row.local_role;
            return h("div", {},
              h("div", { class: "row row-wrap" },
                h("strong", {}, row.display_name || row.username),
                row.owner ? badge(t("access.users.owner"), "warn") : null),
              h("div", { class: "small muted mono" }, `@${row.username}`),
              row.local_account
                ? h("div", { class: "chips" },
                    badge(`${t("access.users.localAccount")}: ${row.local_account}`, "muted"),
                    role ? badge(role, "ok") : null)
                : null);
          },
        },
        {
          key: "preset_name", label: t("access.users.preset"),
          render: (row) => badge(row.preset_name || presetName(data, row.preset_id), row.preset_name ? "neutral" : "muted"),
        },
        {
          key: "enabled", label: t("access.users.status"), width: "130px",
          render: (row) => row.enabled === false
            ? badge(t("access.users.disabled"), "muted")
            : badge(t("access.users.active"), "ok"),
        },
        {
          key: "active_sessions", label: t("access.users.sessions"), width: "100px", align: "right",
          render: (row) => h("span", { class: "num mono" }, fmtNumber(row.active_sessions || 0, 0)),
        },
        {
          key: "updated_at", label: t("access.users.updated"), width: "140px",
          render: (row) => h("span", { class: "small muted" }, row.updated_at ? row.updated_at.slice(0, 16).replace("T", " ") : t("common.dash")),
        },
        {
          key: "actions", label: t("access.actions"), width: "260px", sortable: false,
          render: (row) => {
            if (!canTouchUser(row)) {
              return h("span", { class: "small muted" }, t("access.users.ownerOnlyShort"));
            }
            return h("div", { class: "row row-wrap" },
              button(t("common.buttons.edit"), () => editUser(row), { size: "sm", kind: "ghost" }),
              button(row.enabled === false ? t("common.buttons.enable") : t("common.buttons.disable"),
                () => toggleUser(row), { size: "sm", kind: "ghost" }),
              button(t("common.buttons.delete"), () => removeUser(row), { size: "sm", kind: "danger" }));
          },
        },
      ],
      rows: data.users,
      empty: t("access.users.empty"),
    }));
    return host;
  };

  /* ------------------------------------------------------------------ */
  /*  Пресеты                                                            */
  /* ------------------------------------------------------------------ */

  const editPreset = (preset = null) => {
    const nameInput = input({ value: preset ? preset.name : "", maxlength: "60", placeholder: t("access.presets.namePlaceholder") });
    const descInput = input({ value: preset ? preset.description || "" : "", maxlength: "300", placeholder: t("access.presets.descPlaceholder") });
    const controls = new Map();
    const grid = h("div", { class: "permission-grid" });
    for (const [section, key] of SECTIONS) {
      const current = preset && preset.permissions ? preset.permissions[section] : (section === "dashboard" ? "view" : "none");
      const control = select(LEVELS.map((item) => ({ value: item.value, label: t(item.key) })), { value: current });
      controls.set(section, control);
      grid.append(h("div", { class: "permission-row" },
        h("span", { class: "permission-name" }, t(key)), control));
    }
    const body = h("div", { class: "col" },
      h("div", { class: "grid cards-2" },
        field(t("access.presets.name"), nameInput, t("access.presets.nameHint")),
        field(t("access.presets.description"), descInput, t("access.presets.descHint"))),
      h("div", { class: "permission-title" }, t("access.presets.permissions")),
      h("p", { class: "small muted" }, t("access.presets.permissionsHint")),
      grid);

    modal({
      title: preset ? t("access.presets.editTitle") : t("access.presets.addTitle"),
      width: 760,
      body,
      actions: [
        { label: t("common.buttons.cancel") },
        {
          label: t("common.buttons.save"),
          kind: "primary",
          onClick: async (close) => {
            const name = nameInput.value.trim();
            if (!name) {
              toast(t("access.presets.nameRequired"), "warn");
              return false;
            }
            const permissions = {};
            for (const [section, control] of controls) {
              if (control.value !== "none") permissions[section] = control.value;
            }
            if (!Object.keys(permissions).length) {
              toast(t("access.presets.permissionsRequired"), "warn");
              return false;
            }
            const entry = {
              id: preset ? preset.id : "",
              name,
              description: descInput.value.trim(),
              permissions,
            };
            const presets = data.presets.map((item) => ({
              id: item.id, name: item.name, description: item.description || "",
              permissions: item.permissions || {},
            }));
            const index = presets.findIndex((item) => item.id && item.id === entry.id);
            if (index === -1) presets.push(entry);
            else presets[index] = entry;
            if (await savePresets(presets)) close();
            return false;
          },
        },
      ],
    });
  };

  const removePreset = async (preset) => {
    if (preset.user_count) {
      toast(t("access.presets.inUse"), "warn");
      return;
    }
    const ok = await confirmDialog(t("access.presets.deleteConfirm", { name: preset.name }), {
      title: t("access.presets.deleteTitle"), okLabel: t("common.buttons.delete"),
    });
    if (!ok) return;
    await savePresets(data.presets
      .filter((item) => item.id !== preset.id)
      .map((item) => ({
        id: item.id, name: item.name, description: item.description || "",
        permissions: item.permissions || {},
      })));
  };

  const drawPresets = () => {
    const host = h("div", { class: "col" });
    if (!data.presets.length) {
      mount(host, empty(t("access.presets.empty"), {
        mark: "◇",
        hint: t("access.presets.emptyHint"),
        extra: button(t("access.presets.add"), () => editPreset(), { kind: "primary", size: "sm" }),
      }));
      return host;
    }
    mount(host, table({
      columns: [
        {
          key: "name", label: t("access.presets.name"),
          render: (row) => h("div", {},
            h("strong", {}, row.name),
            row.description ? h("div", { class: "small muted" }, row.description) : null),
        },
        {
          key: "permissions", label: t("access.presets.permissions"),
          render: (row) => {
            const values = Object.values(row.permissions || {});
            const view = values.filter((level) => level === "view").length;
            const manage = values.filter((level) => level === "manage").length;
            return h("div", { class: "chips" },
              view ? badge(t("access.presets.viewCount", { n: view }), "muted") : null,
              manage ? badge(t("access.presets.manageCount", { n: manage }), "ok") : null,
              !values.length ? badge(t("access.level.none"), "muted") : null);
          },
        },
        {
          key: "user_count", label: t("access.presets.users"), width: "100px", align: "right",
          render: (row) => h("span", { class: "num mono" }, fmtNumber(row.user_count || 0, 0)),
        },
        {
          key: "actions", label: t("access.actions"), width: "170px", sortable: false,
          render: (row) => {
            if (!canManagePrivileged() && elevatedPreset(row)) {
              return h("span", { class: "small muted" }, t("access.users.ownerOnlyShort"));
            }
            return h("div", { class: "row row-wrap" },
              button(t("common.buttons.edit"), () => editPreset(row), { size: "sm", kind: "ghost" }),
              button(t("common.buttons.delete"), () => removePreset(row), {
                size: "sm", kind: "danger", disabled: Boolean(row.user_count),
              }));
          },
        },
      ],
      rows: data.presets,
      empty: t("access.presets.empty"),
    }));
    return host;
  };

  /* ------------------------------------------------------------------ */
  /*  Отрисовка                                                          */
  /* ------------------------------------------------------------------ */

  const draw = () => {
    const tabbar = tabs([
      { id: "users", label: t("access.tabs.users"), count: data.users.length },
      { id: "presets", label: t("access.tabs.presets"), count: data.presets.length },
    ], { active: activeTab, onChange: (id) => { activeTab = id; draw(); } });
    mount(tabsHost, tabbar);
    mount(headHost, h("div", { class: "toolbar" },
      button(t("access.refresh"), load, { size: "sm", kind: "ghost" }),
      h("div", { class: "spacer" }),
      activeTab === "users"
        ? button(t("access.users.add"), () => editUser(), { size: "sm", kind: "primary" })
        : button(t("access.presets.add"), () => editPreset(), { size: "sm", kind: "primary" })));
    mount(bodyHost, activeTab === "users" ? drawUsers() : drawPresets());
  };

  const modeHost = h("span", { class: "muted small" });
  const head = h("div", { class: "view-head" },
    h("div", {},
      h("h1", { class: "view-title" }, t("access.title")),
      h("p", { class: "muted" }, t("access.subtitle"))),
    h("div", { class: "toolbar" },
      can("settings", true)
        ? button(t("nav.settings.title"), () => (location.hash = "#/settings"), { size: "sm", kind: "ghost" })
        : null,
      modeHost));

  const authNotice = h("div");
  const updateNotice = () => {
    modeHost.textContent = data.auth_enabled ? t("access.mode.on") : t("access.mode.off");
    if (data.auth_enabled) {
      mount(authNotice, data.local_only ? notice(t("access.localOnly"), "ok") : null);
    } else {
      mount(authNotice, notice(t("access.authOff"), "warn"));
    }
  };

  mount(root, head, authNotice, h("div", { class: "view-head" }, h("div"), headHost), tabsHost, bodyHost);
  await load();
  updateNotice();
  draw();

  return () => {};
}
