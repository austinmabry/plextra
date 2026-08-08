"use strict";

/* ------------------------------------------------------------------ utils */

const $ = (id) => document.getElementById(id);

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function csvToList(value) {
  return (value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function csvToInts(value) {
  return csvToList(value)
    .map((part) => parseInt(part, 10))
    .filter((n) => !Number.isNaN(n));
}

const listToCsv = (items) => (items || []).join(", ");

function when(ts) {
  if (!ts) return "—";
  const date = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function relative(ts) {
  if (!ts) return "never";
  const seconds = Math.round((Date.now() - ts * 1000) / 1000);
  const future = seconds < 0;
  const abs = Math.abs(seconds);
  const units = [["d", 86400], ["h", 3600], ["m", 60]];
  for (const [suffix, size] of units) {
    if (abs >= size) {
      const n = Math.floor(abs / size);
      return future ? `in ${n}${suffix}` : `${n}${suffix} ago`;
    }
  }
  return future ? "shortly" : "just now";
}

let toastTimer = null;
function toast(message, kind) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast ${kind || ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 5000);
}

/* -------------------------------------------------------------------- api */

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)sidecarr_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function api(path, options) {
  const response = await fetch(path, {
    ...(options || {}),
    headers: {
      "Content-Type": "application/json",
      // The server checks this against the cookie a cross-origin page cannot read.
      "X-CSRF-Token": csrfToken(),
      ...((options || {}).headers || {}),
    },
  });
  if (response.status === 401) {
    showLogin();
    throw new Error("Authentication required");
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = null;
  }
  if (!response.ok) {
    throw new Error((data && data.detail) || `HTTP ${response.status}`);
  }
  return data;
}

const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body || {}) });
const put = (path, body) =>
  api(path, { method: "PUT", body: JSON.stringify(body || {}) });

/* ------------------------------------------------------------------ state */

const state = {
  view: "dashboard",
  config: null,
  providers: [],
  meta: { radarr: null, sonarr: null },
  logCursor: 0,
  editing: null,
  timer: null,
};

// These have a home of their own on the Source model; everything else a
// provider declares is stored in source.options.
const NAMED_SOURCE_FIELDS = new Set(["account", "list_url", "person", "period"]);

const providerByKey = (key) => state.providers.find((p) => p.key === key) || null;

function sourcesFor(providerKey, mediaType) {
  const provider = providerByKey(providerKey);
  if (!provider) return [];
  return provider.sources.filter((s) => s.media.includes(mediaType));
}

/* -------------------------------------------------------------- auth flow */

function showLogin() {
  $("login").classList.remove("hidden");
  $("app").classList.add("hidden");
}

async function boot() {
  const status = await api("/api/auth/status");
  if (status.auth_required && !status.authenticated) {
    showLogin();
    return;
  }
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("logout").classList.toggle("hidden", !status.auth_required);
  await Promise.all([loadConfig(), loadProviders()]);
  switchView(state.view);
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("login-error");
  error.classList.add("hidden");
  try {
    await post("/api/auth/login", { password: $("login-password").value });
    $("login-password").value = "";
    await boot();
  } catch (exc) {
    error.textContent = exc.message;
    error.classList.remove("hidden");
  }
});

$("logout").addEventListener("click", async () => {
  await post("/api/auth/logout");
  location.reload();
});

/* ------------------------------------------------------------------ views */

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("hidden", section.id !== `view-${view}`);
  });

  clearInterval(state.timer);
  const renderers = {
    dashboard: renderDashboard,
    lists: renderLists,
    history: renderHistory,
    settings: renderSettings,
    logs: renderLogs,
  };
  renderers[view]();

  if (view === "logs") state.timer = setInterval(pollLogs, 2000);
  if (view === "dashboard") state.timer = setInterval(renderDashboard, 8000);
  if (view === "lists") state.timer = setInterval(renderLists, 8000);
}

async function loadConfig() {
  state.config = await api("/api/config");
}

async function loadProviders() {
  state.providers = (await api("/api/providers")).providers || [];
}

/* -------------------------------------------------------------- dashboard */

async function renderDashboard() {
  let status;
  try {
    status = await api("/api/status");
  } catch (exc) {
    return toast(exc.message, "err");
  }
  $("version").textContent = `v${status.version}`;

  const cards = [
    ["Trakt", status.trakt.configured ? `${status.trakt.accounts.length} account(s)` : "Not set up", status.trakt.configured],
    // "Configured", not "Connected" — this says the settings are filled in, not
    // that the server answered. Use Test in Settings to check reachability.
    ["Radarr", status.radarr.configured ? "Configured" : status.radarr.enabled ? "Incomplete" : "Disabled", status.radarr.configured],
    ["Sonarr", status.sonarr.configured ? "Configured" : status.sonarr.enabled ? "Incomplete" : "Disabled", status.sonarr.configured],
    ["Lists", `${status.lists.enabled} of ${status.lists.total} enabled`, status.lists.enabled > 0],
    ["Titles added", String(status.totals.added), true],
    ["Syncs run", String(status.totals.runs), true],
  ];

  $("status-cards").replaceChildren(
    ...cards.map(([label, value, ok]) =>
      el("div", { class: "stat" },
        el("div", { class: "label" }, label),
        el("div", { class: "value", style: ok ? "" : "color:var(--muted)" }, value))
    )
  );

  $("dashboard-runs").replaceChildren(runsTable(status.recent_runs, false));

  const paused = status.scheduler_paused;
  const toggle = $("scheduler-toggle");
  toggle.textContent = paused ? "Resume scheduler" : "Pause scheduler";
  toggle.className = paused ? "primary" : "ghost";
}

$("scheduler-toggle").addEventListener("click", async () => {
  const paused = $("scheduler-toggle").textContent.startsWith("Pause");
  try {
    await put("/api/scheduler", { paused });
    toast(paused ? "Scheduler paused." : "Scheduler resumed.", "ok");
    renderDashboard();
  } catch (exc) {
    toast(exc.message, "err");
  }
});

/* ------------------------------------------------------------------ theme */

function applyTheme(theme) {
  // No stored preference means follow the OS, which the stylesheet does on its own.
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
}

function currentTheme() {
  const stored = localStorage.getItem("sidecarr-theme");
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

applyTheme(localStorage.getItem("sidecarr-theme"));

$("theme-toggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  localStorage.setItem("sidecarr-theme", next);
  applyTheme(next);
});

/* ------------------------------------------------------------------ lists */

function sourceLabel(job) {
  const source = job.source || {};
  const provider = providerByKey(source.provider || "trakt");
  const providerName = provider ? provider.name : source.provider || "?";
  const type = (provider ? provider.sources.find((s) => s.key === source.type) : null);
  const typeLabel = type ? type.label.toLowerCase() : source.type;

  // Show the identifying detail too, so two lists from the same provider are
  // told apart at a glance.
  const detail =
    source.list_url ||
    source.person ||
    (source.options && (source.options.url || source.options.list_id || source.options.chart)) ||
    "";
  return detail
    ? `${providerName} · ${typeLabel}: ${detail}`
    : `${providerName} · ${typeLabel}`;
}

function scheduleLabel(job) {
  const schedule = job.schedule || {};
  if (schedule.type === "manual") return "manual";
  if (schedule.type === "cron") return `cron ${schedule.cron}`;
  return `every ${schedule.hours}h`;
}

async function renderLists() {
  let payload;
  try {
    payload = await api("/api/lists");
  } catch (exc) {
    return toast(exc.message, "err");
  }

  if (!payload.lists.length) {
    $("lists-body").replaceChildren(
      el("div", { class: "empty" }, "No lists yet. Add one to start syncing Trakt into Radarr or Sonarr.")
    );
    return;
  }

  const rows = payload.lists.map((job) => {
    const last = (job.last_run || [])[0];
    return el("tr", {},
      el("td", {},
        el("div", {}, job.name),
        el("div", { class: "muted tiny" }, sourceLabel(job))),
      el("td", {}, job.media_type === "movie" ? "Movies" : "Shows"),
      el("td", {}, scheduleLabel(job)),
      el("td", {}, job.enabled ? (job.next_run ? when(job.next_run) : "—") : el("span", { class: "pill idle" }, "disabled")),
      el("td", {},
        job.running
          ? el("span", { class: "pill warn" }, "running")
          : last
            ? el("span", { class: `pill ${statusClass(last.status)}` }, `${last.status} · ${last.added} added`)
            : el("span", { class: "pill idle" }, "never run"),
        job.queued
          ? el("div", { class: "muted tiny" }, `${job.queued} queued`)
          : null),
      el("td", { class: "actions" },
        el("button", { class: "tiny-btn", onclick: () => runJob(job.id, false), disabled: job.running || null }, "Run"),
        el("button", { class: "tiny-btn ghost", onclick: () => runJob(job.id, true), disabled: job.running || null }, "Dry run"),
        el("button", { class: "tiny-btn ghost", onclick: () => openEditor(job) }, "Edit"),
        el("button", { class: "tiny-btn ghost danger", onclick: () => deleteJob(job) }, "Delete"))
    );
  });

  $("lists-body").replaceChildren(
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "List"), el("th", {}, "Type"), el("th", {}, "Schedule"),
        el("th", {}, "Next run"), el("th", {}, "Last result"), el("th", {}))),
      el("tbody", {}, ...rows))
  );
}

function statusClass(status) {
  if (status === "success") return "ok";
  if (status === "partial") return "warn";
  if (status === "running") return "idle";
  return "err";
}

async function runJob(listId, dryRun) {
  try {
    await post(`/api/lists/${listId}/run?dry_run=${dryRun}`);
    toast(dryRun ? "Dry run started — watch the Logs tab." : "Sync started.", "ok");
    setTimeout(renderLists, 1500);
  } catch (exc) {
    toast(exc.message, "err");
  }
}

async function deleteJob(job) {
  if (!confirm(`Delete the list "${job.name}"? Titles already added stay in Radarr/Sonarr.`)) return;
  try {
    await api(`/api/lists/${job.id}`, { method: "DELETE" });
    toast("List deleted.", "ok");
    renderLists();
  } catch (exc) {
    toast(exc.message, "err");
  }
}

/* ---------------------------------------------------------------- history */

$("refresh-history").addEventListener("click", renderHistory);

function runsTable(runs, clickable) {
  if (!runs || !runs.length) {
    return el("div", { class: "empty" }, "Nothing has run yet.");
  }
  const rows = runs.map((run) =>
    el("tr", {
      style: clickable ? "cursor:pointer" : "",
      onclick: clickable ? () => showRunItems(run) : null,
    },
      el("td", {},
        el("div", {}, run.list_name),
        el("div", { class: "muted tiny" }, when(run.started_at))),
      el("td", {}, el("span", { class: `pill ${statusClass(run.status)}` }, run.status)),
      el("td", {}, run.dry_run ? "dry run" : "live"),
      el("td", {}, String(run.added)),
      el("td", {}, `${run.existing} present · ${run.filtered} filtered · ${run.excluded} excluded`),
      el("td", {}, run.failed ? el("span", { class: "error" }, `${run.failed} failed`) : ""),
      el("td", { class: "muted" }, run.message || ""))
  );
  return el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "List"), el("th", {}, "Status"), el("th", {}, "Mode"),
      el("th", {}, "Added"), el("th", {}, "Skipped"), el("th", {}, "Failed"), el("th", {}, "Detail"))),
    el("tbody", {}, ...rows));
}

async function renderHistory() {
  try {
    const payload = await api("/api/runs?limit=50");
    $("history-body").replaceChildren(runsTable(payload.runs, true));
    $("run-detail").replaceChildren();
  } catch (exc) {
    toast(exc.message, "err");
  }
}

async function showRunItems(run) {
  try {
    const payload = await api(`/api/runs/${run.id}/items`);
    const rows = payload.items.map((item) =>
      el("tr", {},
        el("td", {}, item.title),
        el("td", {}, el("span", { class: `pill ${item.action === "added" ? "ok" : item.action === "failed" ? "err" : "idle"}` }, item.action)),
        el("td", { class: "muted" }, item.reason || ""))
    );
    $("run-detail").replaceChildren(
      el("h3", {}, `${run.list_name} — ${when(run.started_at)}`),
      rows.length
        ? el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "Title"), el("th", {}, "Outcome"), el("th", {}, "Reason"))),
            el("tbody", {}, ...rows))
        : el("div", { class: "empty" }, "No per-title records for this run.")
    );
  } catch (exc) {
    toast(exc.message, "err");
  }
}

/* ------------------------------------------------------------------- logs */

async function renderLogs() {
  state.logCursor = 0;
  $("log-body").replaceChildren();
  await pollLogs();
}

async function pollLogs() {
  let payload;
  try {
    payload = await api(`/api/logs?after=${state.logCursor}`);
  } catch (_) {
    return;
  }
  if (!payload.logs.length) return;
  state.logCursor = payload.cursor;

  const body = $("log-body");
  for (const entry of payload.logs) {
    body.append(
      el("div", { class: `lvl-${entry.level}` },
        el("span", { class: "ts" }, new Date(entry.ts * 1000).toLocaleTimeString() + "  "),
        `${entry.level.padEnd(7)} ${entry.message}`)
    );
  }
  if ($("log-follow").checked) body.scrollTop = body.scrollHeight;
}

/* --------------------------------------------------------------- settings */

function fillSelect(select, options, selected, placeholder) {
  select.replaceChildren();
  if (placeholder !== undefined) {
    select.append(el("option", { value: "" }, placeholder));
  }
  for (const option of options) {
    const node = el("option", { value: String(option.value) }, option.label);
    if (String(option.value) === String(selected)) node.selected = true;
    select.append(node);
  }
}

function fillMultiSelect(select, options, selectedValues) {
  const chosen = new Set((selectedValues || []).map(String));
  select.replaceChildren();
  for (const option of options) {
    const node = el("option", { value: String(option.value) }, option.label);
    if (chosen.has(String(option.value))) node.selected = true;
    select.append(node);
  }
}

const selectedValues = (select) =>
  Array.from(select.selectedOptions).map((option) => parseInt(option.value, 10));

async function ensureMeta(kind, force) {
  if (state.meta[kind] && !force) return state.meta[kind];
  try {
    state.meta[kind] = await post(`/api/${kind}/test`, {});
  } catch (_) {
    state.meta[kind] = null;
  }
  return state.meta[kind];
}

function applyArrMeta(kind, meta) {
  const config = state.config[kind];
  const profiles = (meta?.quality_profiles || []).map((p) => ({ value: p.id, label: p.name }));
  const roots = (meta?.root_folders || []).map((f) => ({ value: f.path, label: f.path }));
  const tags = (meta?.tags || []).map((t) => ({ value: t.id, label: t.label }));

  fillSelect($(`${kind}-profile`), profiles, config.quality_profile_id, "— choose —");
  fillSelect($(`${kind}-root`), roots, config.root_folder, "— choose —");
  fillMultiSelect($(`${kind}-tags`), tags, config.tags);

  if (kind === "sonarr") {
    const languages = (meta?.language_profiles || []).map((p) => ({ value: p.id, label: p.name }));
    fillSelect($("sonarr-language"), languages, config.language_profile_id, "— none —");
    // Sonarr v4 removed language profiles, so hide the field once we know the
    // server has none rather than offering an option that does nothing.
    $("sonarr-language-label").classList.toggle("hidden", !!meta && languages.length === 0);
  }
}

async function renderSettings() {
  await loadConfig();
  const config = state.config;

  $("trakt-client-id").value = config.trakt.client_id || "";
  $("trakt-client-secret").value = config.trakt.client_secret || "";
  renderTraktAccounts(config.trakt.accounts || []);

  for (const entry of PROVIDER_CREDENTIALS) {
    $(entry.field).value = entry.read(config) || "";
  }

  for (const kind of ["radarr", "sonarr"]) {
    const section = config[kind];
    $(`${kind}-enabled`).checked = section.enabled;
    $(`${kind}-url`).value = section.url || "";
    $(`${kind}-api-key`).value = section.api_key || "";
    $(`${kind}-monitored`).checked = section.monitored;
    $(`${kind}-search`).checked = section.search_on_add;
  }
  $("radarr-availability").value = config.radarr.minimum_availability;
  $("sonarr-monitor").value = config.sonarr.monitor;
  $("sonarr-series-type").value = config.sonarr.series_type;
  $("sonarr-season-folder").checked = config.sonarr.season_folder;

  await renderPacing();

  // Load both in parallel: an unreachable instance should not make the other
  // one wait for its timeout.
  await Promise.all(["radarr", "sonarr"].map(async (kind) => {
    const usable = config[kind].enabled && config[kind].url && config[kind].api_key;
    applyArrMeta(kind, usable ? await ensureMeta(kind) : null);
  }));
}

async function renderPacing() {
  let pacing;
  try {
    pacing = await api("/api/pacing");
  } catch (exc) {
    return;
  }
  $("pacing-enabled").checked = pacing.enabled;
  $("pacing-max").value = pacing.max_adds;
  $("pacing-window").value = pacing.window_minutes;

  const parts = [];
  if (pacing.enabled) {
    const perHour = Math.round((pacing.max_adds * 60) / pacing.window_minutes);
    parts.push(`About ${perHour} titles an hour.`);
    parts.push(`${pacing.used_in_window} of ${pacing.max_adds} used in the current window.`);
  }
  if (pacing.queued) {
    parts.push(`${pacing.queued} title${pacing.queued === 1 ? "" : "s"} waiting in the queue.`);
  } else if (pacing.enabled) {
    parts.push("Nothing waiting.");
  }
  $("pacing-summary").textContent = parts.join(" ");
}

function renderTraktAccounts(accounts) {
  const container = $("trakt-accounts");
  if (!accounts.length) {
    container.replaceChildren(el("p", { class: "muted" }, "No Trakt account connected yet."));
    return;
  }
  container.replaceChildren(
    el("table", {},
      el("tbody", {}, ...accounts.map((name) =>
        el("tr", {},
          el("td", {}, name),
          el("td", { class: "actions" },
            el("button", {
              class: "tiny-btn ghost danger",
              onclick: async () => {
                if (!confirm(`Disconnect the Trakt account ${name}?`)) return;
                await api(`/api/trakt/accounts/${encodeURIComponent(name)}`, { method: "DELETE" });
                renderSettings();
              },
            }, "Disconnect")))))
    )
  );
}

$("save-trakt").addEventListener("click", async () => {
  try {
    await put("/api/config/trakt", {
      client_id: $("trakt-client-id").value,
      client_secret: $("trakt-client-secret").value,
    });
    await loadConfig();
    toast("Trakt application saved.", "ok");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

$("test-trakt").addEventListener("click", async () => {
  try {
    const result = await post("/api/trakt/test");
    toast(result.ok ? "Trakt client ID works." : "Trakt rejected the client ID.", result.ok ? "ok" : "err");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

$("connect-trakt").addEventListener("click", async () => {
  const panel = $("device-code");
  try {
    const device = await post("/api/trakt/device/start");
    panel.classList.remove("hidden");
    panel.replaceChildren(
      el("div", {}, "Open ", el("a", { href: device.verification_url, target: "_blank", rel: "noreferrer" }, device.verification_url), " and enter:"),
      el("div", { class: "code" }, device.user_code),
      el("div", { class: "muted tiny" }, "Waiting for you to approve it…")
    );
    pollDevice(device, Date.now() + device.expires_in * 1000, device.interval * 1000);
  } catch (exc) {
    toast(exc.message, "err");
  }
});

async function pollDevice(device, deadline, interval) {
  const panel = $("device-code");
  if (Date.now() > deadline) {
    panel.replaceChildren(el("div", { class: "error" }, "The code expired. Try again."));
    return;
  }
  let result;
  try {
    result = await post("/api/trakt/device/poll", { device_code: device.device_code });
  } catch (exc) {
    panel.replaceChildren(el("div", { class: "error" }, exc.message));
    return;
  }

  if (result.status === "ok") {
    panel.classList.add("hidden");
    toast(`Connected the Trakt account ${result.username}.`, "ok");
    renderSettings();
    return;
  }
  const fatal = {
    expired: "The code expired. Try again.",
    denied: "You denied the request.",
    invalid: "Trakt did not recognise that code.",
    used: "That code was already used. Start again.",
  };
  if (fatal[result.status]) {
    panel.replaceChildren(el("div", { class: "error" }, fatal[result.status]));
    return;
  }
  const wait = result.status === "slow_down" ? interval + 1000 : interval;
  setTimeout(() => pollDevice(device, deadline, wait), wait);
}

for (const kind of ["radarr", "sonarr"]) {
  $(`test-${kind}`).addEventListener("click", async () => {
    try {
      const meta = await post(`/api/${kind}/test`, {
        url: $(`${kind}-url`).value,
        api_key: $(`${kind}-api-key`).value,
      });
      state.meta[kind] = meta;
      applyArrMeta(kind, meta);
      toast(`${meta.app_name} ${meta.version} reachable.`, "ok");
    } catch (exc) {
      toast(exc.message, "err");
    }
  });
}

$("save-radarr").addEventListener("click", async () => {
  try {
    await put("/api/config/radarr", {
      enabled: $("radarr-enabled").checked,
      url: $("radarr-url").value.trim(),
      api_key: $("radarr-api-key").value.trim(),
      quality_profile_id: parseInt($("radarr-profile").value, 10) || null,
      root_folder: $("radarr-root").value,
      minimum_availability: $("radarr-availability").value,
      tags: selectedValues($("radarr-tags")),
      monitored: $("radarr-monitored").checked,
      search_on_add: $("radarr-search").checked,
    });
    await loadConfig();
    await ensureMeta("radarr", true);
    toast("Radarr settings saved.", "ok");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

$("save-sonarr").addEventListener("click", async () => {
  try {
    await put("/api/config/sonarr", {
      enabled: $("sonarr-enabled").checked,
      url: $("sonarr-url").value.trim(),
      api_key: $("sonarr-api-key").value.trim(),
      quality_profile_id: parseInt($("sonarr-profile").value, 10) || null,
      language_profile_id: parseInt($("sonarr-language").value, 10) || null,
      root_folder: $("sonarr-root").value,
      season_folder: $("sonarr-season-folder").checked,
      series_type: $("sonarr-series-type").value,
      monitor: $("sonarr-monitor").value,
      tags: selectedValues($("sonarr-tags")),
      monitored: $("sonarr-monitored").checked,
      search_on_add: $("sonarr-search").checked,
    });
    await loadConfig();
    await ensureMeta("sonarr", true);
    toast("Sonarr settings saved.", "ok");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

$("save-pacing").addEventListener("click", async () => {
  try {
    await put("/api/pacing", {
      enabled: $("pacing-enabled").checked,
      max_adds: parseInt($("pacing-max").value, 10) || 10,
      window_minutes: parseInt($("pacing-window").value, 10) || 10,
    });
    await renderPacing();
    toast("Add rate saved.", "ok");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

$("clear-queue").addEventListener("click", async () => {
  if (!confirm("Discard every title waiting in the queue? They will be picked up again on the next sync of their list.")) return;
  try {
    const result = await api("/api/pacing/queue", { method: "DELETE" });
    await renderPacing();
    toast(`Cleared ${result.dropped} queued title${result.dropped === 1 ? "" : "s"}.`, "ok");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

$("save-password").addEventListener("click", async () => {
  const password = $("web-password").value;
  try {
    const result = await put("/api/auth/password", { password });
    $("web-password").value = "";
    $("logout").classList.toggle("hidden", !result.auth_required);
    toast(result.auth_required ? "Password set." : "Password removed.", result.auth_required ? "ok" : "err");
  } catch (exc) {
    toast(exc.message, "err");
  }
});

/* ----------------------------------------------------------- list editor */

function renderProviderChoices(selected) {
  const media = $("f-media").value;
  const options = state.providers
    // Hide a provider that has nothing for this media type at all.
    .filter((p) => p.media.includes(media))
    .map((p) => ({
      value: p.key,
      label: p.configured ? p.name : `${p.name} — needs setup`,
    }));
  fillSelect($("f-provider"), options, selected || "trakt");
  if (!$("f-provider").value && options.length) $("f-provider").selectedIndex = 0;
}

function renderSourceChoices(selected) {
  const media = $("f-media").value;
  const sources = sourcesFor($("f-provider").value, media);
  fillSelect(
    $("f-source-type"),
    sources.map((s) => ({ value: s.key, label: s.label })),
    selected
  );
  if (!$("f-source-type").value && sources.length) $("f-source-type").selectedIndex = 0;
}

/** Build the provider-specific inputs from the descriptor. */
function renderSourceFields(job) {
  const provider = providerByKey($("f-provider").value);
  const source = provider
    ? provider.sources.find((s) => s.key === $("f-source-type").value)
    : null;
  const container = $("source-fields");
  container.replaceChildren();

  const note = [];
  if (provider && !provider.configured && provider.setup_hint) note.push(provider.setup_hint);
  if (source && source.help) note.push(source.help);
  $("provider-note").textContent = note.join(" ");

  // Account picker, for providers that hold more than one.
  const accounts = (provider && provider.accounts) || [];
  const needsAccount = source && source.needs_account;
  $("row-account").classList.toggle("hidden", accounts.length === 0 && !needsAccount);
  if (!$("row-account").classList.contains("hidden")) {
    fillSelect(
      $("f-account"),
      accounts.map((a) => ({ value: a, label: a })),
      (job && job.source && job.source.account) || "",
      needsAccount ? "— pick an account —" : "— none (public only) —"
    );
    // These sources only exist for a signed-in user, so preselect rather than
    // letting the sync fail later with "needs a connected account".
    if (needsAccount && !$("f-account").value && $("f-account").options.length > 1) {
      $("f-account").selectedIndex = 1;
    }
  }

  $("row-picker").classList.toggle("hidden", !(provider && provider.can_pick_lists));
  $("list-picker").classList.add("hidden");

  if (!source) return;
  for (const fieldSpec of source.fields) {
    container.append(buildSourceField(fieldSpec, job));
  }
}

function buildSourceField(spec, job) {
  const stored = readSourceValue(job, spec.key);
  const value = stored || spec.default || "";

  let input;
  if (spec.kind === "select") {
    input = el("select", { id: `sf-${spec.key}`, "data-source-key": spec.key });
    for (const choice of spec.choices) {
      const option = el("option", { value: choice.value }, choice.label);
      if (String(choice.value) === String(value)) option.selected = true;
      input.append(option);
    }
  } else if (spec.kind === "textarea") {
    input = el("textarea", {
      id: `sf-${spec.key}`,
      "data-source-key": spec.key,
      placeholder: spec.placeholder || "",
      rows: 8,
    });
    input.value = value;
  } else {
    input = el("input", {
      type: "text",
      id: `sf-${spec.key}`,
      "data-source-key": spec.key,
      placeholder: spec.placeholder || "",
      value,
    });
  }

  return el(
    "div",
    { class: "field-row" },
    el(
      "label",
      {},
      spec.label,
      spec.help ? el("span", { class: "muted tiny" }, spec.help) : null,
      input
    )
  );
}

function readSourceValue(job, key) {
  if (!job || !job.source) return "";
  if (NAMED_SOURCE_FIELDS.has(key)) return job.source[key] || "";
  return (job.source.options || {})[key] || "";
}

function collectSourceFields() {
  const source = {
    provider: $("f-provider").value,
    type: $("f-source-type").value,
    account: $("row-account").classList.contains("hidden") ? "" : $("f-account").value,
    options: {},
  };
  for (const input of $("source-fields").querySelectorAll("[data-source-key]")) {
    const key = input.dataset.sourceKey;
    const value = input.value.trim();
    if (NAMED_SOURCE_FIELDS.has(key)) {
      source[key] = value;
    } else if (value) {
      source.options[key] = value;
    }
  }
  return source;
}

function updateEditorVisibility() {
  const media = $("f-media").value;
  $("row-networks").classList.toggle("hidden", media !== "show");
  $("id-hint").textContent = media === "movie" ? "TMDb" : "TVDb";

  const schedule = $("f-sched-type").value;
  $("row-hours").classList.toggle("hidden", schedule !== "interval");
  $("row-cron").classList.toggle("hidden", schedule !== "cron");
}

$("f-media").addEventListener("change", async () => {
  // Changing movies/shows can invalidate both the provider and the source, so
  // rebuild the whole chain rather than leaving an impossible combination.
  renderProviderChoices($("f-provider").value);
  renderSourceChoices($("f-source-type").value);
  renderSourceFields(null);
  updateEditorVisibility();
  await loadEditorOverrides();
});

$("f-provider").addEventListener("change", () => {
  renderSourceChoices();
  renderSourceFields(null);
});

$("f-source-type").addEventListener("change", () => renderSourceFields(null));
$("f-sched-type").addEventListener("change", updateEditorVisibility);

async function loadEditorOverrides(job) {
  const kind = $("f-media").value === "movie" ? "radarr" : "sonarr";
  const meta = await ensureMeta(kind);
  const profiles = (meta?.quality_profiles || []).map((p) => ({ value: p.id, label: p.name }));
  const roots = (meta?.root_folders || []).map((f) => ({ value: f.path, label: f.path }));
  const tags = (meta?.tags || []).map((t) => ({ value: t.id, label: t.label }));

  fillSelect($("f-profile"), profiles, job?.quality_profile_id ?? "", "Inherit");
  fillSelect($("f-root"), roots, job?.root_folder ?? "", "Inherit");
  fillMultiSelect($("f-tags"), tags, job?.tags || []);
}

function openEditor(job) {
  state.editing = job || null;
  $("editor-title").textContent = job ? "Edit list" : "Add list";

  const source = job?.source || {};
  const filters = job?.filters || {};
  const schedule = job?.schedule || {};

  $("f-name").value = job?.name || "";
  $("f-media").value = job?.media_type || "movie";
  renderProviderChoices(source.provider || "trakt");
  renderSourceChoices(source.type || "");
  renderSourceFields(job);
  $("f-limit").value = job?.limit ?? 0;
  $("f-sort").value = job?.sort || "none";
  $("f-sched-type").value = schedule.type || "interval";
  $("f-hours").value = schedule.hours ?? 24;
  $("f-cron").value = schedule.cron || "0 3 * * *";
  $("f-enabled").checked = job ? job.enabled : true;
  $("f-dry-run").checked = job?.dry_run || false;
  $("f-search").value = job == null || job.search_on_add === null ? "" : job.search_on_add ? "yes" : "no";

  $("f-min-year").value = filters.min_year ?? 0;
  $("f-max-year").value = filters.max_year ?? 0;
  $("f-min-runtime").value = filters.min_runtime ?? 0;
  $("f-max-runtime").value = filters.max_runtime ?? 0;
  $("f-min-rating").value = filters.min_rating ?? 0;
  $("f-min-votes").value = filters.min_votes ?? 0;
  $("f-countries").value = listToCsv(filters.allowed_countries);
  $("f-languages").value = listToCsv(filters.allowed_languages);
  $("f-genres").value = listToCsv(filters.blacklisted_genres);
  $("f-networks").value = listToCsv(filters.blacklisted_networks);
  $("f-keywords").value = listToCsv(filters.blacklisted_title_keywords);
  $("f-ids").value = listToCsv(filters.blacklisted_ids);

  updateEditorVisibility();
  loadEditorOverrides(job);
  $("editor").classList.remove("hidden");
}

$("new-list").addEventListener("click", () => openEditor(null));
$("editor-close").addEventListener("click", closeEditor);
$("editor-cancel").addEventListener("click", closeEditor);

function closeEditor() {
  $("editor").classList.add("hidden");
  state.editing = null;
}

$("pick-list").addEventListener("click", async () => {
  const picker = $("list-picker");
  const providerKey = $("f-provider").value;
  const provider = providerByKey(providerKey);
  picker.classList.remove("hidden");
  picker.replaceChildren(el("div", { class: "empty" }, `Loading your ${provider?.name} lists…`));

  try {
    const account = $("row-account").classList.contains("hidden") ? "" : $("f-account").value;
    const payload = await api(
      `/api/providers/${providerKey}/lists?account=${encodeURIComponent(account)}`
    );
    if (!payload.lists.length) {
      picker.replaceChildren(el("div", { class: "empty" }, "No lists found for that account."));
      return;
    }
    picker.replaceChildren(...payload.lists.map((item) =>
      el("button", {
        type: "button",
        onclick: () => {
          // Jump to whichever source type actually takes a list reference.
          const listSource = sourcesFor(providerKey, $("f-media").value)
            .find((s) => s.fields.some((f) => f.key === "list_url"));
          if (listSource) {
            $("f-source-type").value = listSource.key;
            renderSourceFields(null);
          }
          const input = $("sf-list_url");
          if (input) input.value = item.url;
          picker.classList.add("hidden");
        },
      },
        el("strong", {}, item.name),
        el("span", { class: "muted tiny" },
          `  ${item.url} · ${item.item_count} items${item.owned ? "" : " · liked"}`))
    ));
  } catch (exc) {
    picker.replaceChildren(el("div", { class: "empty error" }, exc.message));
  }
});

$("editor-save").addEventListener("click", async () => {
  const searchValue = $("f-search").value;
  const payload = {
    name: $("f-name").value.trim() || "Untitled list",
    enabled: $("f-enabled").checked,
    media_type: $("f-media").value,
    source: collectSourceFields(),
    limit: parseInt($("f-limit").value, 10) || 0,
    sort: $("f-sort").value,
    filters: {
      allowed_countries: csvToList($("f-countries").value),
      allowed_languages: csvToList($("f-languages").value),
      blacklisted_genres: csvToList($("f-genres").value),
      blacklisted_networks: csvToList($("f-networks").value),
      blacklisted_title_keywords: csvToList($("f-keywords").value),
      blacklisted_ids: csvToInts($("f-ids").value),
      min_year: parseInt($("f-min-year").value, 10) || 0,
      max_year: parseInt($("f-max-year").value, 10) || 0,
      min_runtime: parseInt($("f-min-runtime").value, 10) || 0,
      max_runtime: parseInt($("f-max-runtime").value, 10) || 0,
      min_rating: parseFloat($("f-min-rating").value) || 0,
      min_votes: parseInt($("f-min-votes").value, 10) || 0,
    },
    schedule: {
      type: $("f-sched-type").value,
      hours: parseFloat($("f-hours").value) || 24,
      cron: $("f-cron").value.trim() || "0 3 * * *",
    },
    dry_run: $("f-dry-run").checked,
    search_on_add: searchValue === "" ? null : searchValue === "yes",
    quality_profile_id: parseInt($("f-profile").value, 10) || null,
    root_folder: $("f-root").value,
    tags: selectedValues($("f-tags")),
  };

  try {
    if (state.editing) {
      payload.id = state.editing.id;
      await put(`/api/lists/${state.editing.id}`, payload);
    } else {
      await post("/api/lists", payload);
    }
    closeEditor();
    toast("List saved.", "ok");
    renderLists();
  } catch (exc) {
    toast(exc.message, "err");
  }
});

/* ------------------------------------------- provider credentials (settings) */

const PROVIDER_CREDENTIALS = [
  { key: "tmdb", field: "tmdb-api-key", body: (v) => ({ api_key: v }), read: (c) => c.tmdb.api_key },
  { key: "mdblist", field: "mdblist-api-key", body: (v) => ({ api_key: v }), read: (c) => c.mdblist.api_key },
  { key: "plex", field: "plex-token", body: (v) => ({ token: v }), read: (c) => c.plex.token },
];

for (const entry of PROVIDER_CREDENTIALS) {
  $(`save-${entry.key}`).addEventListener("click", async () => {
    try {
      await put(`/api/config/${entry.key}`, entry.body($(entry.field).value.trim()));
      await Promise.all([loadConfig(), loadProviders()]);
      toast(`${providerByKey(entry.key)?.name || entry.key} settings saved.`, "ok");
    } catch (exc) {
      toast(exc.message, "err");
    }
  });

  $(`test-${entry.key}`).addEventListener("click", async () => {
    try {
      // Save first, so Test checks what is in the box rather than what was
      // saved last time.
      await put(`/api/config/${entry.key}`, entry.body($(entry.field).value.trim()));
      const result = await post(`/api/providers/${entry.key}/test`);
      await loadProviders();
      toast(`${result.provider} works.`, "ok");
    } catch (exc) {
      toast(exc.message, "err");
    }
  });
}

/* ------------------------------------------------------------------- init */

boot().catch((exc) => toast(exc.message, "err"));
