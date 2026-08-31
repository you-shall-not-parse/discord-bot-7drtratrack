const state = { data: null, view: "rollcalls" };
const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

async function loadDashboard() {
  const initialLoad = state.data === null;
  $("#loading").hidden = !initialLoad;
  $("#error").hidden = true;
  if (initialLoad) {
    $("#rollcalls").hidden = true;
    $("#trainees").hidden = true;
  }
  try {
    const response = await fetch("/api/dashboard", {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(20_000)
    });
    if (!response.ok) throw new Error(response.status === 503 ? "The bot is still connecting to Discord." : `Service returned ${response.status}.`);
    state.data = await response.json();
    render();
    $("#sync-status").textContent = "Live from Discord";
    $("#last-updated").textContent = `LAST SYNC ${new Date(state.data.generated_at).toLocaleString("en-GB")}`;
  } catch (error) {
    $("#loading").hidden = true;
    $("#error").hidden = !initialLoad;
    $("#error-message").textContent = error.name === "TimeoutError"
      ? "The live report took too long to respond. Please try again."
      : error.message;
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
      <div class="card-footer"><span>${rollcall.summary.rate}% response · ${rollcall.departed_count} archived</span><a class="detail-button" href="/rollcalls/${encodeURIComponent(rollcall.key)}">Full report</a></div>
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
        <div class="card-footer"><span>${track.check_labels.map(escapeHtml).join(" · ")}</span><a class="detail-button" href="/trainees/${encodeURIComponent(track.key)}">Open tracker</a></div>
      </article>`;
  }).join("");
}

function showView(view) {
  state.view = view;
  document.querySelectorAll(".dashboard-view").forEach(section => section.hidden = section.id !== view);
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.view === view));
}

document.addEventListener("click", event => {
  const tab = event.target.closest(".tab");
  if (tab) showView(tab.dataset.view);
});
$("#retry").addEventListener("click", loadDashboard);
loadDashboard();
setInterval(loadDashboard, 60_000);
