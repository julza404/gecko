const app = document.getElementById("app");
const toast = document.getElementById("toast");
const syncState = document.getElementById("syncState");
const viewKicker = document.getElementById("viewKicker");

let dashboard = null;
let currentView = "today";
let calendarMode = "week";
let toastTimer = null;

const typeLabels = {
  strategic: "Strategic",
  "hands-on": "Hands-on",
  admin: "Admin",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value, options = {}) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: options.weekday ?? "long",
    month: options.month ?? "short",
    day: options.day ?? "numeric",
    year: options.year ?? "numeric",
    timeZone: "America/Los_Angeles",
  }).format(new Date(`${value}T12:00:00-07:00`));
}

function formatTime(value) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
  }).format(new Date(value));
}

function minutesLabel(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours && rest) return `${hours}h ${rest}m`;
  if (hours) return `${hours}h`;
  return `${rest}m`;
}

function showToast(message, error = false) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  toastTimer = setTimeout(() => { toast.className = "toast"; }, 2600);
}

function setSync(mode) {
  syncState.classList.toggle("error", mode === "error");
  syncState.innerHTML = `<i></i>${mode === "error" ? "OFFLINE" : mode === "saving" ? "SAVING" : "LIVE"}`;
}

async function request(path, options = {}) {
  setSync(options.method ? "saving" : "live");
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    setSync("error");
    throw new Error(body.error || "Gecko could not complete that action.");
  }
  setSync("live");
  return body;
}

async function loadDashboard({ quiet = false } = {}) {
  try {
    dashboard = await request(`/api/dashboard?cache=${Date.now()}`);
    render();
    if (!quiet) showToast("Gecko is current.");
  } catch (error) {
    if (!dashboard) app.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

async function mutate(path, method, payload, successMessage) {
  try {
    dashboard = await request(path, {
      method,
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    render();
    if (successMessage) showToast(successMessage);
  } catch (error) {
    showToast(error.message, true);
    await loadDashboard({ quiet: true });
  }
}

function updateChrome() {
  if (!dashboard) return;
  document.getElementById("currentDate").textContent = formatDate(dashboard.date);
  document.getElementById("todayCount").textContent = dashboard.tasks.length;
  document.getElementById("backlogCount").textContent = dashboard.tasks.filter(task => !task.done).length;
  document.getElementById("agentStamp").textContent = `Updated ${formatTime(dashboard.agent.lastUpdated)}`;
  document.querySelectorAll("[data-view]").forEach(button => {
    button.classList.toggle("active", button.dataset.view === currentView);
  });
  const labels = {
    today: "TODAY / LIVE PLAN",
    calendar: "CALENDAR / VERIFIED + PLANNED",
    backlog: "BACKLOG / OPEN ACTIONS",
    email: "EMAIL / ACTION CAPTURE",
    reset: "REVIEW / THIS WEEK",
    archive: "ARCHIVE / REAL HISTORY",
  };
  viewKicker.textContent = labels[currentView];
}

function sourceLine(task) {
  const source = task.source || {};
  const pieces = [
    `${task.estimateMinutes || 30}m`,
    task.planning === "planned" ? "planned" : "unplanned",
    source.sender || source.label || source.kind,
  ].filter(Boolean);
  return pieces.join(" · ");
}

function taskTypeOptions(selected) {
  return Object.entries(typeLabels).map(([value, label]) =>
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`
  ).join("");
}

function taskRows(tasks) {
  if (!tasks.length) return `<li class="empty-state">No actions recorded for today.</li>`;
  return tasks.map(task => `
    <li class="task-row ${task.done ? "done" : ""}" data-task-id="${escapeHtml(task.id)}">
      <input class="task-check" type="checkbox" ${task.done ? "checked" : ""} data-action="toggle-task" aria-label="${task.done ? "Reopen" : "Complete"} ${escapeHtml(task.title)}" />
      <div class="task-copy">
        <strong>${escapeHtml(task.title)}</strong>
        <small>${escapeHtml(sourceLine(task))}</small>
      </div>
      <select class="bucket-select ${task.type}" data-action="change-type" aria-label="Category for ${escapeHtml(task.title)}">
        ${taskTypeOptions(task.type)}
      </select>
      <button class="row-button frog-choice ${dashboard.frog?.id === task.id ? "selected" : ""}" data-action="set-frog" title="Make this the frog" aria-label="Make this the frog">◎</button>
      <button class="row-button delete" data-action="delete-task" title="Delete task" aria-label="Delete ${escapeHtml(task.title)}">×</button>
    </li>
  `).join("");
}

function metric(type, label) {
  const value = dashboard.metrics[type];
  return `
    <div class="metric ${type}">
      <strong>${value.completed} / ${value.pending}</strong>
      <span>${label}</span>
      <small>completed / pending today</small>
    </div>`;
}

function historyStrip() {
  if (!dashboard.history.length) return `<div class="empty-state">History starts with today.</div>`;
  return `<div class="history-strip">${dashboard.history.map(day => `
    <div class="history-day ${day.frogDone ? "frog-done" : ""} ${day.date === dashboard.date ? "today" : ""}" title="${formatDate(day.date)}">
      ${new Date(`${day.date}T12:00:00`).getDate()}
    </div>
  `).join("")}</div>`;
}

function renderToday() {
  const capacity = dashboard.capacity;
  const focusRatio = capacity.workdayMinutes
    ? Math.min(100, Math.round(capacity.focusMinutes / capacity.workdayMinutes * 100))
    : 0;
  const frog = dashboard.frog;
  return `
    <section class="intro-grid">
      <div class="headline">
        <h1>One clear priority.<br />A truthful day.</h1>
        <p>Gecko has ${minutesLabel(capacity.focusMinutes)} of realistic focus capacity against ${minutesLabel(capacity.busyMinutes)} of verified calendar load.</p>
      </div>
      <div class="capacity-panel">
        <header><span>REALISTIC FOCUS</span><strong>${minutesLabel(capacity.focusMinutes)}</strong></header>
        <div class="capacity-track"><i style="width:${focusRatio}%"></i></div>
        <p>${minutesLabel(capacity.busyMinutes)} busy · ${minutesLabel(capacity.reserveMinutes)} reserve · ${dashboard.tasks.filter(task => !task.done).length} open actions</p>
      </div>
    </section>

    <section class="frog-panel ${dashboard.frogDone ? "complete" : ""} ${frog ? "" : "frog-empty"}">
      <div class="frog-signal">G</div>
      <div>
        <span class="eyebrow">TODAY'S FROG${frog ? ` / ${escapeHtml(typeLabels[frog.type].toUpperCase())}` : ""}</span>
        <h2>${frog ? escapeHtml(frog.title) : "Choose one meaningful action as today’s frog."}</h2>
        <p>${frog ? `${frog.estimateMinutes} minutes · ${escapeHtml(frog.priority)} priority` : "Use the target control beside an action."}</p>
      </div>
      <div class="frog-actions">
        ${frog ? `<button data-action="toggle-frog">${dashboard.frogDone ? "Reopen frog" : "Mark frog complete"}</button>` : ""}
      </div>
    </section>

    <section class="workspace">
      <div>
        <div class="section-heading"><h2>Today, in order</h2><span>${dashboard.tasks.length} RECORDED</span></div>
        <ul class="task-list">${taskRows(dashboard.tasks)}</ul>
        <form class="capture-form" id="taskForm">
          <input name="title" required autocomplete="off" placeholder="Capture a new action" aria-label="New action" />
          <select name="type" aria-label="New action category">${taskTypeOptions("admin")}</select>
          <select class="estimate" name="estimateMinutes" aria-label="Time estimate">
            <option value="15">15 min</option><option value="30" selected>30 min</option><option value="45">45 min</option><option value="60">60 min</option><option value="90">90 min</option>
          </select>
          <button class="primary-button" title="Add action" aria-label="Add action">+</button>
        </form>
      </div>

      <aside class="record-panel">
        <div class="section-heading"><h3>Today’s record</h3><span>LIVE</span></div>
        <div class="metrics-grid">
          ${metric("strategic", "Strategic")}
          ${metric("hands-on", "Hands-on")}
          ${metric("admin", "Admin")}
          <div class="metric afk">
            <strong>${dashboard.afk.minutes}m</strong>
            <span>AFK today</span>
            <small>${dashboard.afk.lockCount} locks after 9 AM</small>
          </div>
        </div>
        <div class="record-note">
          <strong>${dashboard.history.length === 1 ? "The record starts today." : `${dashboard.history.length} real days recorded.`}</strong>
          <p>No seeded streaks or retrospective estimates. Gecko builds this history only from saved daily activity.</p>
          ${historyStrip()}
        </div>
      </aside>
    </section>`;
}

function dateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function monthCalendar() {
  const current = new Date(`${dashboard.date}T12:00:00`);
  const first = new Date(current.getFullYear(), current.getMonth(), 1, 12);
  const start = new Date(first);
  start.setDate(first.getDate() - first.getDay());
  const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const eventMap = {};
  [...dashboard.calendar.events.map(event => ({ ...event, className: "busy" })), ...dashboard.calendar.plannedBlocks].forEach(event => {
    const key = event.start.slice(0, 10);
    (eventMap[key] ||= []).push(event);
  });
  const cells = Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    const key = dateKey(day);
    const muted = day.getMonth() !== current.getMonth();
    return `<div class="month-cell ${muted ? "muted" : ""} ${key === dashboard.date ? "today" : ""}">
      <span class="month-number">${day.getDate()}</span>
      ${(eventMap[key] || []).map(event => `<span class="calendar-chip ${event.className || event.type}">${escapeHtml(event.title)}</span>`).join("")}
    </div>`;
  }).join("");
  return `<div class="month-grid">${names.map(name => `<div class="month-name">${name}</div>`).join("")}${cells}</div>`;
}

function mondayFor(value) {
  const day = new Date(`${value}T12:00:00`);
  const offset = (day.getDay() + 6) % 7;
  day.setDate(day.getDate() - offset);
  return day;
}

function weekCalendar() {
  const monday = mondayFor(dashboard.date);
  const days = Array.from({ length: 5 }, (_, index) => {
    const day = new Date(monday);
    day.setDate(monday.getDate() + index);
    return day;
  });
  const entries = [...dashboard.calendar.events.map(event => ({ ...event, className: "busy" })), ...dashboard.calendar.plannedBlocks];
  let html = `<div class="week-board"><div class="week-head">TIME</div>`;
  html += days.map(day => `<div class="week-head">${day.toLocaleDateString("en-US", { weekday: "short" }).toUpperCase()} ${day.getDate()}</div>`).join("");
  for (let hour = 9; hour < 17; hour += 1) {
    html += `<div class="week-time">${String(hour).padStart(2, "0")}:00</div>`;
    for (const day of days) {
      const key = dateKey(day);
      const matching = entries.filter(event => event.start.slice(0, 10) === key && new Date(event.start).getHours() === hour);
      html += `<div>${matching.map(event =>
        `<span class="week-event ${event.className || event.type}">${escapeHtml(formatTime(event.start))}<br>${escapeHtml(event.title)}</span>`
      ).join("")}</div>`;
    }
  }
  return `${html}</div>`;
}

function renderCalendar() {
  const current = new Date(`${dashboard.date}T12:00:00`);
  const monthTitle = current.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  return `
    <section class="view-header">
      <div><h1>Gecko’s calendar</h1><p>Verified busy time stays fixed. Gecko places open actions into the remaining workday without writing back to Outlook.</p></div>
      <div class="view-actions"><label class="secondary-button" for="calendarFile">Import calendar JSON</label><input id="calendarFile" type="file" accept=".json,application/json" hidden /></div>
    </section>
    <div class="calendar-controls">
      <strong>${escapeHtml(monthTitle)}</strong>
      <div class="segmented"><button data-action="calendar-mode" data-mode="week" class="${calendarMode === "week" ? "active" : ""}">Week</button><button data-action="calendar-mode" data-mode="month" class="${calendarMode === "month" ? "active" : ""}">Month</button></div>
    </div>
    ${calendarMode === "week" ? weekCalendar() : monthCalendar()}
    <div class="calendar-legend"><span style="color:#52677f">Verified busy</span><span style="color:var(--strategic-ink)">Strategic</span><span style="color:var(--hands-ink)">Hands-on</span><span style="color:var(--admin-ink)">Admin</span></div>`;
}

function renderBacklog() {
  const tasks = dashboard.tasks.filter(task => !task.done);
  return `
    <section class="view-header"><div><h1>Open actions</h1><p>The backlog is the full set of real, unfinished work. Gecko carries these actions forward when the day rolls over.</p></div></section>
    <div class="table-list">
      ${tasks.length ? tasks.map(task => `
        <div class="table-row" data-task-id="${escapeHtml(task.id)}">
          <div><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(sourceLine(task))}${task.carryCount ? ` · carried ${task.carryCount}x` : ""}</small></div>
          <span class="tag ${task.type}">${escapeHtml(typeLabels[task.type])}</span>
          <button class="secondary-button" data-action="set-frog">Make frog</button>
        </div>`).join("") : `<div class="empty-state">No open actions.</div>`}
    </div>`;
}

function renderEmail() {
  return `
    <section class="view-header"><div><h1>Turn email into action.</h1><p>Capture the email’s subject and source, then assign the work before it enters today’s plan.</p></div></section>
    <div class="email-layout">
      <form class="form-panel" id="emailForm">
        <div class="form-grid">
          <div class="form-group wide"><label for="emailSubject">Action / subject</label><input id="emailSubject" name="subject" required placeholder="What needs your input?" /></div>
          <div class="form-group"><label for="emailSender">From</label><input id="emailSender" name="sender" placeholder="Sender or team" /></div>
          <div class="form-group"><label for="emailUrl">Outlook link</label><input id="emailUrl" name="url" type="url" placeholder="Optional message link" /></div>
          <div class="form-group"><label for="emailType">Bucket</label><select id="emailType" name="type">${taskTypeOptions("admin")}</select></div>
          <div class="form-group"><label for="emailEstimate">Estimate</label><select id="emailEstimate" name="estimateMinutes"><option value="15">15 min</option><option value="30" selected>30 min</option><option value="45">45 min</option><option value="60">60 min</option><option value="90">90 min</option></select></div>
          <div class="form-group wide"><label for="emailNote">Context</label><textarea id="emailNote" name="note" placeholder="Decision, due date, or next step"></textarea></div>
        </div>
        <div class="form-footer"><button class="primary-button">Add email action</button></div>
      </form>
      <aside class="source-note"><strong>Lightweight by design</strong><p>This records the message reference and task locally. It does not authenticate to Outlook, read your mailbox, or send mail.</p></aside>
    </div>`;
}

function renderReset() {
  const weekly = dashboard.weekly;
  return `
    <section class="view-header">
      <div><h1>Weekly reset</h1><p>${escapeHtml(formatDate(weekly.weekStart, { weekday: undefined, month: "short", day: "numeric", year: undefined }))} through ${escapeHtml(formatDate(weekly.weekEnd, { weekday: undefined, month: "short", day: "numeric", year: undefined }))}</p></div>
      <button class="primary-button" data-action="weekly-reset">Write weekly reset</button>
    </section>
    <div class="reset-metrics">
      <div class="reset-stat"><strong>${weekly.daysRecorded}</strong><span>real days recorded</span></div>
      <div class="reset-stat"><strong>${weekly.frogsCompleted} / ${weekly.frogDays}</strong><span>frogs completed</span></div>
      <div class="reset-stat"><strong>${weekly.plannedCompleted}</strong><span>planned completed</span></div>
      <div class="reset-stat"><strong>${weekly.unplannedCompleted}</strong><span>unplanned completed</span></div>
      <div class="reset-stat"><strong>${weekly.afkMinutes}m</strong><span>AFK recorded</span></div>
    </div>
    <div class="signal-panel"><span>GECKO SIGNAL</span><p>${escapeHtml(weekly.signal)}</p></div>`;
}

function renderArchive() {
  return `
    <section class="view-header"><div><h1>Archive</h1><p>Only days Gecko has actually recorded appear here.</p></div></section>
    <div class="table-list">
      ${dashboard.history.length ? [...dashboard.history].reverse().map(day => `
        <div class="table-row">
          <div><strong>${escapeHtml(formatDate(day.date))}</strong><small>${day.closed ? "Day closed" : "Current day"} · ${day.afkMinutes}m AFK</small></div>
          <span class="tag ${day.frogDone ? "strategic" : "admin"}">${day.frogDone ? "Frog done" : "Frog open"}</span>
          <span>${day.completed} done / ${day.pending} open</span>
        </div>`).join("") : `<div class="empty-state">History starts today.</div>`}
    </div>`;
}

function render() {
  if (!dashboard) {
    app.innerHTML = `<div class="loading">CONNECTING TO GECKO</div>`;
    return;
  }
  updateChrome();
  const views = {
    today: renderToday,
    calendar: renderCalendar,
    backlog: renderBacklog,
    email: renderEmail,
    reset: renderReset,
    archive: renderArchive,
  };
  app.innerHTML = views[currentView]();
}

document.querySelectorAll("[data-view]").forEach(button => {
  button.addEventListener("click", () => {
    currentView = button.dataset.view;
    history.replaceState(null, "", `#${currentView}`);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

document.querySelectorAll("[data-view-link]").forEach(link => {
  link.addEventListener("click", event => {
    event.preventDefault();
    currentView = link.dataset.viewLink;
    history.replaceState(null, "", `#${currentView}`);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

document.getElementById("refreshButton").addEventListener("click", () => loadDashboard());

app.addEventListener("submit", async event => {
  if (event.target.id === "taskForm") {
    event.preventDefault();
    const form = new FormData(event.target);
    await mutate("/api/tasks", "POST", {
      title: form.get("title"),
      type: form.get("type"),
      estimateMinutes: Number(form.get("estimateMinutes")),
    }, "Action added to today.");
  }
  if (event.target.id === "emailForm") {
    event.preventDefault();
    const form = new FormData(event.target);
    await mutate("/api/email/import", "POST", Object.fromEntries(form.entries()), "Email action added.");
    if (dashboard && currentView === "email") event.target.reset();
  }
});

app.addEventListener("change", async event => {
  const row = event.target.closest("[data-task-id]");
  if (event.target.matches('[data-action="toggle-task"]') && row) {
    await mutate(`/api/tasks/${row.dataset.taskId}`, "PATCH", { done: event.target.checked }, event.target.checked ? "Action completed." : "Action reopened.");
  }
  if (event.target.matches('[data-action="change-type"]') && row) {
    await mutate(`/api/tasks/${row.dataset.taskId}`, "PATCH", { type: event.target.value }, "Bucket saved.");
  }
  if (event.target.id === "calendarFile") {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const events = JSON.parse(await file.text());
      await mutate("/api/calendar/import", "POST", { date: dashboard.date, events }, "Verified calendar imported.");
    } catch (error) {
      showToast(`Calendar import failed: ${error.message}`, true);
    }
  }
});

app.addEventListener("click", async event => {
  const action = event.target.closest("[data-action]");
  if (!action) return;
  const row = action.closest("[data-task-id]");
  if (action.dataset.action === "delete-task" && row) {
    await mutate(`/api/tasks/${row.dataset.taskId}`, "DELETE", undefined, "Action deleted.");
  }
  if (action.dataset.action === "set-frog" && row) {
    await mutate("/api/frog", "POST", { taskId: row.dataset.taskId }, "Today’s frog updated.");
  }
  if (action.dataset.action === "toggle-frog") {
    await mutate("/api/frog/toggle", "POST", {}, dashboard.frogDone ? "Frog reopened." : "Frog completed.");
  }
  if (action.dataset.action === "calendar-mode") {
    calendarMode = action.dataset.mode;
    render();
  }
  if (action.dataset.action === "weekly-reset") {
    try {
      const result = await request("/api/weekly-reset", { method: "POST", body: "{}" });
      dashboard = result.dashboard;
      render();
      showToast("Weekly reset written to this-week.md.");
    } catch (error) {
      showToast(error.message, true);
    }
  }
});

window.addEventListener("focus", () => loadDashboard({ quiet: true }));
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadDashboard({ quiet: true });
});
setInterval(() => {
  if (!document.hidden) loadDashboard({ quiet: true });
}, 30_000);

const initialView = location.hash.slice(1);
if (["today", "calendar", "backlog", "email", "reset", "archive"].includes(initialView)) currentView = initialView;
window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1);
  if (["today", "calendar", "backlog", "email", "reset", "archive"].includes(view)) {
    currentView = view;
    render();
  }
});
render();
loadDashboard({ quiet: true });
