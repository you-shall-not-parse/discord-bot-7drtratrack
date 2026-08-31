const $ = selector => document.querySelector(selector);
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));
const reportState = { item: null, kind: null, sortKey: null, direction: 1 };

function reportLocation() {
  const parts = location.pathname.split("/").filter(Boolean);
  return { kind: parts[0] === "rollcalls" ? "rollcall" : "trainee", key: decodeURIComponent(parts[1] || "") };
}

function dateLabel(iso) {
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" })
    .format(new Date(`${iso}T00:00:00Z`));
}

function compareValues(left, right) {
  if (typeof left === "number" && typeof right === "number") return left - right;
  if (typeof left === "boolean" && typeof right === "boolean") return Number(left) - Number(right);
  return String(left ?? "").localeCompare(String(right ?? ""), "en-GB", { numeric: true, sensitivity: "base" });
}

function sortedRows(rows, columns) {
  const column = columns.find(entry => entry.key === reportState.sortKey) || columns[0];
  return [...rows].sort((left, right) => {
    const primary = compareValues(column.value(left), column.value(right)) * reportState.direction;
    return primary || String(left.name || "").localeCompare(String(right.name || ""));
  });
}

function sortableTable(rows, columns, emptyMessage) {
  const sorted = sortedRows(rows, columns);
  const headers = columns.map(column => {
    const active = reportState.sortKey === column.key;
    const arrow = active ? (reportState.direction === 1 ? "▲" : "▼") : "";
    return `<th class="${column.current ? "current-week" : ""}" aria-sort="${active ? (reportState.direction === 1 ? "ascending" : "descending") : "none"}">
      <button class="sort-button ${active ? "active" : ""}" data-sort="${escapeHtml(column.key)}"><span class="sort-arrow">${arrow}</span>${escapeHtml(column.label)}</button>
    </th>`;
  }).join("");
  const body = sorted.map(row => `<tr>${columns.map(column => `<td class="${column.current ? "current-week" : ""}">${column.render(row)}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table class="report-table"><thead><tr>${headers}</tr></thead><tbody>${body || `<tr><td colspan="${columns.length}">${escapeHtml(emptyMessage)}</td></tr>`}</tbody></table></div>`;
}

function rollcallStatus(value) {
  if (value === "✅") return { order: 0, className: "attending", label: "attending" };
  if (value === "🅾️") return { order: 1, className: "other-rollcall", label: "attending elsewhere" };
  return { order: 2, className: "missing", label: "missing" };
}

function rollcallColumns(item) {
  return [
    { key: "rank", label: "Rank", value: row => row.rank_order, render: row => `<span class="rank-cell">${escapeHtml(row.rank)}</span>` },
    { key: "name", label: "Nickname", value: row => row.name, render: row => escapeHtml(row.name) },
    {
      key: "current_status",
      label: "Current status",
      value: row => rollcallStatus(row.attendance[item.week]).order,
      render: row => {
        const status = rollcallStatus(row.attendance[item.week]);
        return `<span class="state ${status.className}">${status.label}</span>`;
      }
    },
    { key: "flags", label: "Flags", value: row => row.flags.join(", "), render: row => row.flags.length ? `<span class="flag-left">${escapeHtml(row.flags.join(", "))}</span>` : '<span class="empty-cell">—</span>' },
    ...item.report_columns.map(week => ({
      key: `week:${week}`, label: week, current: week === item.week,
      value: row => row.attendance[week] || "",
      render: row => row.attendance[week] ? `<span class="attendance">${escapeHtml(row.attendance[week])}</span>` : '<span class="attendance empty-cell">—</span>'
    }))
  ];
}

function renderRollcall(item) {
  reportState.sortKey ||= "rank";
  const columns = rollcallColumns(item);
  const active = item.report_members.filter(member => member.active);
  const inactive = item.report_members.filter(member => !member.active);
  $("#report-kicker").textContent = `${item.week} · ${item.locked ? "FINAL" : "IN PROGRESS"}`;
  $("#report-title").textContent = item.title;
  $("#report-description").textContent = "Select any column heading to reorder the full attendance record. Rank follows the 7th Armoured Division hierarchy.";
  $("#report-content").innerHTML = `
    <section class="report-section"><div class="report-section-title"><h2>Roll call</h2><span>${active.length} active members</span></div>${sortableTable(active, columns, "No active members found.")}</section>
    <section class="report-section"><div class="report-section-title"><h2>Inactive</h2><span>${inactive.length} archived members</span></div>${sortableTable(inactive, columns, "No inactive members.")}</section>`;
}

function traineeColumns(item) {
  return [
    {
      key: "name",
      label: "Trainee",
      value: row => `${row.name} ${row.username}`,
      render: row => `<span class="trainee-name">${escapeHtml(row.name)}<small>@${escapeHtml(row.username)}</small></span>`
    },
    { key: "joined", label: "Join date", value: row => row.joined, render: row => dateLabel(row.joined) },
    { key: "review_due", label: `+${item.behind_after_days} days`, value: row => row.review_due, render: row => dateLabel(row.review_due) },
    {
      key: "status",
      label: "Current status",
      value: row => `${row.behind ? 1 : 0}:${row.days}`,
      render: row => `<span class="state ${row.behind ? "behind" : "current"}">${row.behind ? `Behind · ${row.days} days` : `Current · ${row.days} days`}</span>`
    },
    {
      key: "qualifications",
      label: "Qualifications",
      value: row => item.check_labels.filter(label => row.checks[label]).length,
      render: row => `<div class="checks">${item.check_labels.map(label => `<span class="check ${row.checks[label] ? "done" : ""}">${row.checks[label] ? "✓" : "○"} ${escapeHtml(label.replace(" Role", ""))}</span>`).join("")}</div>`
    }
  ];
}

function renderTrainees(item) {
  reportState.sortKey ||= "joined";
  const columns = traineeColumns(item);
  $("#report-kicker").textContent = `${item.behind_after_days}-DAY TRAINING WINDOW`;
  $("#report-title").textContent = item.title;
  $("#report-description").textContent = "Select any column heading to reorder the current Discord trainee roster and qualification status.";
  $("#report-content").innerHTML = `<section class="report-section"><div class="report-section-title"><h2>Trainees</h2><span>${item.members.length} members</span></div>${sortableTable(item.members, columns, "No trainees currently assigned.")}</section>`;
}

function renderReport() {
  if (reportState.kind === "rollcall") renderRollcall(reportState.item);
  else renderTrainees(reportState.item);
}

function renderExportActions(locationInfo) {
  const collection = locationInfo.kind === "rollcall" ? "rollcalls" : "trainees";
  const base = `/exports/${collection}/${encodeURIComponent(locationInfo.key)}`;
  $("#report-actions").innerHTML = `
    <a class="export-button" href="${base}.html" target="_blank" rel="noopener">Open HTML</a>
    <a class="export-button primary" href="${base}.xlsx" download>Download Excel</a>`;
}

document.addEventListener("click", event => {
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  const key = button.dataset.sort;
  if (reportState.sortKey === key) reportState.direction *= -1;
  else { reportState.sortKey = key; reportState.direction = 1; }
  renderReport();
});

async function loadReport() {
  const locationInfo = reportLocation();
  reportState.kind = locationInfo.kind;
  try {
    const response = await fetch("/api/dashboard", { headers: { Accept: "application/json" }, signal: AbortSignal.timeout(20_000) });
    if (response.status === 401) {
      location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
      return;
    }
    if (!response.ok) throw new Error(`Service returned ${response.status}.`);
    const data = await response.json();
    const collection = locationInfo.kind === "rollcall" ? data.rollcalls : data.trainee_tracks;
    reportState.item = collection.find(entry => entry.key === locationInfo.key);
    if (!reportState.item) throw new Error("That report does not exist.");
    renderReport();
    renderExportActions(locationInfo);
    $("#loading").hidden = true;
    $("#report").hidden = false;
    $("#sync-status").textContent = "Live from Discord";
    $("#last-updated").textContent = `LAST SYNC ${new Date(data.generated_at).toLocaleString("en-GB")}`;
    document.title = `${reportState.item.title} — HLL Frontline`;
  } catch (error) {
    $("#loading").hidden = true;
    $("#error").hidden = false;
    $("#error-message").textContent = error.name === "TimeoutError" ? "The report took too long to respond." : error.message;
    $("#sync-status").textContent = "Report unavailable";
  }
}

loadReport();
