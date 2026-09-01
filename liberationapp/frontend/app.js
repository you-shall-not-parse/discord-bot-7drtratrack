const VIEWS = new Set(["overview", "personnel", "server-status", "upcoming", "war-diary", "statistics", "directory"]);
const requestedView = location.hash.replace(/^#/, "");
const state = { data: null, view: VIEWS.has(requestedView) ? requestedView : "overview" };
const $ = selector => document.querySelector(selector);

const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function safeDiscordMediaUrl(value) {
  const safeUrl = safeExternalUrl(value);
  if (!safeUrl) return "";
  const hostname = new URL(safeUrl).hostname.toLowerCase();
  return hostname === "cdn.discordapp.com" || hostname === "media.discordapp.net" || hostname.endsWith(".discordapp.net")
    ? safeUrl
    : "";
}

function externalLink(url, label, className = "detail-button") {
  const safeUrl = safeExternalUrl(url);
  return safeUrl
    ? `<a class="${className}" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
    : "";
}

function formatDate(value, options = {}) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date TBC" : date.toLocaleString("en-GB", {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", ...options
  });
}

function formatDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}h ${String(minutes).padStart(2, "0")}m` : `${minutes}m`;
}

function emptyState(message) {
  return `<div class="inline-empty">${escapeHtml(message)}</div>`;
}

async function loadDashboard() {
  const initialLoad = state.data === null;
  $("#loading").hidden = !initialLoad;
  $("#error").hidden = true;
  if (initialLoad) document.querySelectorAll(".dashboard-view").forEach(section => { section.hidden = true; });
  try {
    const response = await fetch("/api/dashboard", {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(20_000)
    });
    if (response.status === 401) {
      location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
      return;
    }
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
  const traineeTotal = state.data.trainee_tracks.reduce((sum, track) => sum + track.summary.total, 0);
  $("#rollcall-count").textContent = state.data.rollcalls.length;
  $("#trainee-count").textContent = traineeTotal;
  $("#personnel-count").textContent = traineeTotal;
  $("#event-count").textContent = state.data.events.length;
  $("#match-event-count").textContent = state.data.events.length;
  renderOverview();
  renderServerStatus();
  renderRollcalls();
  renderTrainees();
  renderEvents();
  renderWarDiary();
  renderLeaderboards();
  showView(state.view, false);
}

function renderOverview() {
  const nextEvent = state.data.events[0];
  const eventCard = nextEvent ? `
    <article class="feature-card">
      <span class="card-kicker">NEXT EVENT</span><h3>${escapeHtml(nextEvent.name)}</h3>
      <p>${escapeHtml(formatDate(nextEvent.start_time))}${nextEvent.location ? ` · ${escapeHtml(nextEvent.location)}` : ""}</p>
      ${externalLink(nextEvent.url, "Open in Discord")}
    </article>` : `<article class="feature-card"><span class="card-kicker">NEXT EVENT</span><h3>Nothing scheduled</h3><p>No upcoming Discord events are currently listed.</p></article>`;

  const lastResult = state.data.war_diary.recent[0];
  const resultCard = lastResult ? `
    <article class="feature-card latest-result-card">
      <span class="card-kicker">LATEST WAR-DIARY RESULT</span><h3>7DR ${escapeHtml(lastResult.score)} ${escapeHtml(lastResult.opponent)}</h3>
      <p>${escapeHtml(lastResult.date || "Date unavailable")} · ${escapeHtml(lastResult.map)}</p>
      <span class="result ${escapeHtml(lastResult.outcome)}">${escapeHtml(lastResult.outcome)}</span>
    </article>` : `<article class="feature-card"><span class="card-kicker">LATEST WAR-DIARY RESULT</span><h3>No result recorded</h3><p>The latest submitted match will appear here.</p></article>`;

  const holders = state.data.botr.holders;
  const ratCard = `<article class="feature-card rat-card"><span class="card-kicker">RAT OF THE WEEK</span><h3>${holders.length ? holders.map(escapeHtml).join(" · ") : "Awaiting a winner"}</h3><p>${holders.length ? "Current holder of the Rat Of The Week Discord role." : "No current member has the configured role."}</p></article>`;
  $("#overview-grid").innerHTML = eventCard + resultCard + ratCard;

  const links = [
    ["history", "Historical stats", "Browse previous HLL server matches"],
    ["bifrost", "7DR on Bifrost", "Open the external clan leaderboard"],
    ["merch", "7DR merch", "Visit the clan merchandise store"],
    ["twitch", "7DR Twitch", "Watch clan live streams"]
  ];
  $("#quick-links").innerHTML = links.map(([key, title, description]) => {
    const url = safeExternalUrl(state.data.external_links[key]);
    return url ? `<a class="external-card" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
      <span class="card-kicker">EXTERNAL LINK</span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(description)}</small><b aria-hidden="true">↗</b></a>`
      : `<div class="external-card disabled"><span class="card-kicker">LINK TBC</span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(description)}</small></div>`;
  }).join("");
}

function renderServerStatus() {
  const cards = state.data.server_status.map(server => {
    if (server.source === "discord_webhook") {
      const imageUrl = safeDiscordMediaUrl(server.image_url);
      const fields = (server.fields || []).map(field => `
        <div class="webhook-field ${field.inline ? "inline" : ""}"><small>${escapeHtml(field.name)}</small><strong>${escapeHtml(field.value)}</strong></div>`).join("");
      return `<article class="webhook-status-card"${imageUrl ? ` data-server-image="${escapeHtml(imageUrl)}"` : ""}>
        <div class="webhook-card-shade"></div><div class="webhook-card-content">
          <span class="card-kicker">DISCORD WEBHOOK · LIVE STATUS</span><h3>${escapeHtml(server.name)}</h3>
          ${server.content ? `<p class="webhook-copy">${escapeHtml(server.content)}</p>` : ""}
          ${server.description ? `<p class="webhook-copy">${escapeHtml(server.description)}</p>` : ""}
          ${fields ? `<div class="webhook-fields">${fields}</div>` : ""}
          ${server.footer ? `<small class="webhook-footer">${escapeHtml(server.footer)}</small>` : ""}
        </div></article>`;
    }
    return `<article class="feature-card server-card">
      <div class="feature-card-top"><span class="card-kicker">SERVER STATUS</span><span class="badge ${server.available ? "online" : "warn"}">${server.available ? "Online" : "Unavailable"}</span></div>
      <h3>${escapeHtml(server.name)}</h3><p>${server.available ? escapeHtml(server.map) : "Live data could not be reached."}</p>
      <div class="mini-stats"><span><strong>${server.players ?? "—"}</strong> players</span><span><strong>${formatDuration(server.time_remaining_seconds)}</strong> remaining</span></div>
    </article>`;
  }).join("");
  $("#server-grid").innerHTML = cards || emptyState("No server-status webhook or HLL backend is currently available.");
  document.querySelectorAll("[data-server-image]").forEach(card => {
    const imageUrl = safeDiscordMediaUrl(card.dataset.serverImage);
    if (imageUrl) card.style.backgroundImage = `url("${imageUrl.replace(/["\\]/g, "")}")`;
  });
}

function renderRollcalls() {
  $("#rollcall-grid").innerHTML = state.data.rollcalls.map((rollcall, index) => `
    <article class="ops-card" data-index="0${index + 1}">
      <div class="card-top"><div><span class="card-kicker">${escapeHtml(rollcall.week)}</span><h3>${escapeHtml(rollcall.title)}</h3></div><span class="badge ${rollcall.locked ? "" : "warn"}">${rollcall.locked ? "Locked" : "Open"}</span></div>
      <div class="stats"><div class="stat"><strong>${rollcall.summary.total}</strong><small>Expected</small></div><div class="stat"><strong>${rollcall.summary.attending}</strong><small>Attending</small></div><div class="stat"><strong>${rollcall.summary.missing}</strong><small>Missing</small></div></div>
      <div class="progress"><span style="width:${rollcall.summary.rate}%"></span></div>
      <div class="card-footer"><span>${rollcall.summary.rate}% response · ${rollcall.departed_count} archived</span><a class="detail-button" href="/rollcalls/${encodeURIComponent(rollcall.key)}">Full report</a></div>
    </article>`).join("") || emptyState("No roll calls are configured.");
}

function renderTrainees() {
  $("#trainee-grid").innerHTML = state.data.trainee_tracks.map((track, index) => {
    const rate = track.summary.total ? Math.round(track.summary.current / track.summary.total * 100) : 100;
    return `<article class="ops-card" data-index="0${index + 1}">
      <div class="card-top"><div><span class="card-kicker">${track.behind_after_days}-DAY TRAINING WINDOW</span><h3>${escapeHtml(track.title)}</h3></div><span class="badge ${track.summary.behind ? "warn" : ""}">${track.summary.behind ? `${track.summary.behind} overdue` : "On track"}</span></div>
      <div class="stats"><div class="stat"><strong>${track.summary.total}</strong><small>Trainees</small></div><div class="stat"><strong>${track.summary.current}</strong><small>Current</small></div><div class="stat"><strong>${track.summary.behind}</strong><small>Behind</small></div></div>
      <div class="progress"><span style="width:${rate}%"></span></div>
      <div class="card-footer"><span>${track.check_labels.map(escapeHtml).join(" · ")}</span><a class="detail-button" href="/trainees/${encodeURIComponent(track.key)}">Open tracker</a></div>
    </article>`;
  }).join("") || emptyState("No trainee tracks are configured.");
}

function renderEvents() {
  $("#event-grid").innerHTML = state.data.events.map(event => `
    <article class="event-card">
      <time datetime="${escapeHtml(event.start_time)}"><strong>${escapeHtml(new Date(event.start_time).toLocaleDateString("en-GB", { day: "2-digit" }))}</strong><span>${escapeHtml(new Date(event.start_time).toLocaleDateString("en-GB", { month: "short" }))}</span></time>
      <div><span class="card-kicker">${event.status === "active" ? "LIVE NOW" : escapeHtml(formatDate(event.start_time))}</span><h3>${escapeHtml(event.name)}</h3><p>${event.location ? escapeHtml(event.location) : "Location TBC"}${event.interested ? ` · ${event.interested} interested` : ""}</p></div>
      ${externalLink(event.url, "Discord")}
    </article>`).join("") || emptyState("No upcoming Discord events are scheduled.");
}

function resultTable(rows, opponentMode = false) {
  if (!rows.length) return emptyState("No recorded results yet.");
  const body = rows.map(row => opponentMode
    ? `<tr><td>${escapeHtml(row.name)}</td><td>${row.played}</td><td>${row.wins}</td><td>${row.losses}</td><td>${row.draws}</td></tr>`
    : `<tr><td>${escapeHtml(row.date || "—")}</td><td>${escapeHtml(row.opponent)}</td><td>${escapeHtml(row.map)}</td><td><span class="result ${escapeHtml(row.outcome)}">${escapeHtml(row.score)}</span></td></tr>`).join("");
  const headers = opponentMode ? ["Opponent", "P", "W", "L", "D"] : ["Date", "Opponent", "Map", "Result"];
  return `<div class="table-wrap"><table><thead><tr>${headers.map(header => `<th>${header}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderWarDiary() {
  const diary = state.data.war_diary;
  $("#war-record").textContent = `${diary.summary.wins}W · ${diary.summary.losses}L · ${diary.summary.draws}D`;
  $("#war-summary").innerHTML = `
    <div class="stat"><strong>${diary.summary.played}</strong><small>Played</small></div>
    <div class="stat"><strong>${diary.summary.wins}</strong><small>Wins</small></div>
    <div class="stat"><strong>${diary.summary.losses}</strong><small>Losses</small></div>
    <div class="stat"><strong>${diary.summary.draws}</strong><small>Draws</small></div>`;
  $("#recent-results").innerHTML = resultTable(diary.recent);
  $("#opponent-records").innerHTML = resultTable(diary.opponents, true);
}

function renderLeaderboards() {
  $("#leaderboard-groups").innerHTML = state.data.leaderboards.map(group => `
    <section class="leaderboard-group">
      <div class="subsection-heading"><h3>${escapeHtml(group.title)}</h3><span>${group.records.length} categories</span></div>
      <div class="leaderboard-grid">${group.records.map(record => `
        <article class="leader-card"><span class="card-kicker">VERIFIED RECORD</span><h3>${escapeHtml(record.stat)}</h3><ol>${record.leaders.map(leader => `<li><span>${escapeHtml(leader.name)}</span><strong>${escapeHtml(leader.value)}</strong></li>`).join("")}</ol></article>`).join("") || emptyState("No verified records have been submitted yet.")}</div>
    </section>`).join("");
}

function showView(view, updateHash = true) {
  state.view = VIEWS.has(view) ? view : "overview";
  document.querySelectorAll(".dashboard-view").forEach(section => { section.hidden = section.id !== state.view; });
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.view === state.view));
  if (updateHash) history.replaceState(null, "", `#${state.view}`);
}

document.addEventListener("click", event => {
  const tab = event.target.closest(".tab");
  if (tab) showView(tab.dataset.view);
});

$("#hllv-search-form").addEventListener("submit", async event => {
  event.preventDefault();
  const query = $("#hllv-query").value.trim();
  $("#hllv-search-status").textContent = "Searching…";
  $("#hllv-results").innerHTML = "";
  try {
    const response = await fetch(`/api/hllv-search?q=${encodeURIComponent(query)}`, {
      headers: { Accept: "application/json" }, signal: AbortSignal.timeout(10_000)
    });
    if (response.status === 401) {
      location.assign("/login?next=%2F%23directory");
      return;
    }
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Search returned ${response.status}.`);
    $("#hllv-search-status").textContent = `${payload.results.length} result${payload.results.length === 1 ? "" : "s"} for “${payload.query}”.`;
    $("#hllv-results").innerHTML = payload.results.length ? `<div class="directory-results">${payload.results.map(result => `
      <article><span>${escapeHtml(result.discord_name)}</span><strong>${escapeHtml(result.hllv_name)}</strong></article>`).join("")}</div>` : emptyState("No current member matched that search.");
  } catch (error) {
    $("#hllv-search-status").textContent = error.name === "TimeoutError" ? "The search timed out. Try again." : error.message;
  }
});

$("#retry").addEventListener("click", loadDashboard);
loadDashboard();
setInterval(loadDashboard, 60_000);
