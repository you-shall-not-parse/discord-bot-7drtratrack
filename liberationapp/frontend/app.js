const state = { data: null, view: "rollcalls" };
const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

const dateLabel = (iso) => new Intl.DateTimeFormat("en-GB", {
  day: "2-digit", month: "short", year: "numeric"
}).format(new Date(`${iso}T00:00:00Z`));

async function loadDashboard() {
  const initialLoad = state.data === null;
  $("#loading").hidden = !initialLoad;
  $("#error").hidden = true;
  if (initialLoad) {
    $("#rollcalls").hidden = true;
    $("#trainees").hidden = true;
  }
  try {
    const response = await fetch("/api/dashboard", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(response.status === 503 ? "The bot is still connecting to Discord." : `Service returned ${response.status}.`);
    state.data = await response.json();
    render();
    $("#sync-status").textContent = "Live from Discord";
    $("#last-updated").textContent = `LAST SYNC ${new Date(state.data.generated_at).toLocaleString("en-GB")}`;
  } catch (error) {
    $("#loading").hidden = true;
    $("#error").hidden = !initialLoad;
    $("#error-message").textContent = error.message;
    $("#sync-status").textContent = initialLoad ? "Connection interrupted" : "Showing last field report";
  }
}

function render() {
  $("#loading").hidden = true;
  $("#rollcall-count").textContent = state.data.rollcalls.length;
  $("#trainee-count").textContent = state.data.trainee_tracks.reduce((sum, track) => sum + track.summary.total, 0);
  renderRollcalls();
  renderTrainees();
  showView(state.view);
}

function renderRollcalls() {
  $("#rollcall-grid").innerHTML = state.data.rollcalls.map((rollcall, index) => `
    <article class="ops-card" data-index="0${index + 1}">
      <div class="card-top">
        <div><span class="card-kicker">${escapeHtml(rollcall.week)}</span><h3>${escapeHtml(rollcall.title)}</h3></div>
        <span class="badge ${rollcall.locked ? "" : "warn"}">${rollcall.locked ? "Locked" : "Open"}</span>
      </div>
      <div class="stats">
        <div class="stat"><strong>${rollcall.summary.total}</strong><small>Expected</small></div>
        <div class="stat"><strong>${rollcall.summary.attending}</strong><small>Attending</small></div>
        <div class="stat"><strong>${rollcall.summary.missing}</strong><small>Missing</small></div>
      </div>
      <div class="progress"><span style="width:${rollcall.summary.rate}%"></span></div>
      <div class="card-footer"><span>${rollcall.summary.rate}% response · ${rollcall.departed_count} archived</span><button class="detail-button" data-kind="rollcall" data-key="${escapeHtml(rollcall.key)}">Full report</button></div>
    </article>`).join("");
}

function renderTrainees() {
  $("#trainee-grid").innerHTML = state.data.trainee_tracks.map((track, index) => {
    const rate = track.summary.total ? Math.round(track.summary.current / track.summary.total * 100) : 100;
    return `
      <article class="ops-card" data-index="0${index + 1}">
        <div class="card-top">
          <div><span class="card-kicker">${track.behind_after_days}-DAY TRAINING WINDOW</span><h3>${escapeHtml(track.title)}</h3></div>
          <span class="badge ${track.summary.behind ? "warn" : ""}">${track.summary.behind ? `${track.summary.behind} overdue` : "On track"}</span>
        </div>
        <div class="stats">
          <div class="stat"><strong>${track.summary.total}</strong><small>Trainees</small></div>
          <div class="stat"><strong>${track.summary.current}</strong><small>Current</small></div>
          <div class="stat"><strong>${track.summary.behind}</strong><small>Behind</small></div>
        </div>
        <div class="progress"><span style="width:${rate}%"></span></div>
        <div class="card-footer"><span>${track.check_labels.map(escapeHtml).join(" · ")}</span><button class="detail-button" data-kind="trainee" data-key="${escapeHtml(track.key)}">Open tracker</button></div>
      </article>`;
  }).join("");
}

function showView(view) {
  state.view = view;
  document.querySelectorAll(".dashboard-view").forEach(section => section.hidden = section.id !== view);
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.view === view));
}

function openDetail(kind, key) {
  const item = kind === "rollcall"
    ? state.data.rollcalls.find(entry => entry.key === key)
    : state.data.trainee_tracks.find(entry => entry.key === key);
  if (!item) return;
  $("#modal-content").innerHTML = kind === "rollcall" ? rollcallDetail(item) : traineeDetail(item);
  $("#detail-modal").showModal();
}

function rollcallDetail(item) {
  const rows = [...item.members].sort((a, b) => a.name.localeCompare(b.name)).map(member => `
    <tr><td>${escapeHtml(member.name)}</td><td><span class="state ${member.status}">${escapeHtml(member.status.replace("other-rollcall", "attending elsewhere"))}</span></td></tr>`).join("");
  const history = item.history.map(week => `<tr><td>${escapeHtml(week.week)}</td><td>${week.attending}</td><td>${week.partial}</td><td>${week.missing}</td></tr>`).join("");
  return `<p class="eyebrow">${escapeHtml(item.week)} · ${item.locked ? "FINAL" : "IN PROGRESS"}</p>
    <h2>${escapeHtml(item.title)}</h2>
    <div class="table-wrap"><table><thead><tr><th>Member</th><th>Current status</th></tr></thead><tbody>${rows || '<tr><td colspan="2">No members found.</td></tr>'}</tbody></table></div>
    <p class="eyebrow" style="margin-top:30px">12-WEEK HISTORY</p>
    <div class="table-wrap"><table><thead><tr><th>Week</th><th>Attending</th><th>Elsewhere</th><th>Missing</th></tr></thead><tbody>${history || '<tr><td colspan="4">No history yet.</td></tr>'}</tbody></table></div>`;
}

function traineeDetail(item) {
  const rows = item.members.map(member => `
    <tr>
      <td>${escapeHtml(member.name)}<br><small style="color:var(--muted)">@${escapeHtml(member.username)}</small></td>
      <td>${dateLabel(member.joined)}</td><td>${dateLabel(member.review_due)}</td>
      <td><span class="state ${member.behind ? "behind" : "current"}">${member.behind ? `Behind · ${member.days} days` : `Current · ${member.days} days`}</span></td>
      <td><div class="checks">${item.check_labels.map(label => `<span class="check ${member.checks[label] ? "done" : ""}">${member.checks[label] ? "✓" : "○"} ${escapeHtml(label.replace(" Role", ""))}</span>`).join("")}</div></td>
    </tr>`).join("");
  return `<p class="eyebrow">${item.behind_after_days}-DAY TRAINING WINDOW</p><h2>${escapeHtml(item.title)}</h2>
    <div class="table-wrap"><table><thead><tr><th>Trainee</th><th>Joined</th><th>Review due</th><th>Status</th><th>Qualifications</th></tr></thead><tbody>${rows || '<tr><td colspan="5">No trainees currently assigned.</td></tr>'}</tbody></table></div>`;
}

document.addEventListener("click", event => {
  const tab = event.target.closest(".tab");
  if (tab) showView(tab.dataset.view);
  const detail = event.target.closest(".detail-button");
  if (detail) openDetail(detail.dataset.kind, detail.dataset.key);
});
$(".modal-close").addEventListener("click", () => $("#detail-modal").close());
$("#detail-modal").addEventListener("click", event => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("#retry").addEventListener("click", loadDashboard);
loadDashboard();
setInterval(loadDashboard, 60_000);
