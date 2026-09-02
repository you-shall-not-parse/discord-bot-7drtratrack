const VIEWS = new Set(["overview", "personnel", "server-status", "upcoming", "war-diary", "highlights", "statistics", "directory"]);
const requestedView = location.hash.replace(/^#/, "");
const state = { data: null, view: VIEWS.has(requestedView) ? requestedView : "overview", eventLayout: "list" };
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

function statsDate(value, statsUrl) {
  const label = escapeHtml(value || "—");
  const safeUrl = safeExternalUrl(statsUrl);
  return safeUrl
    ? `<a class="match-date" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer" title="Open match stats">${label}</a>`
    : `<span class="match-date">${label}</span>`;
}

function applyMapBackgrounds() {
  document.querySelectorAll("[data-map-image]").forEach(card => {
    try {
      const url = new URL(card.dataset.mapImage, location.origin);
      if (url.origin === location.origin && url.pathname.startsWith("/assets/maps/")) {
        card.style.backgroundImage = `url("${url.href.replace(/["\\]/g, "")}")`;
      }
    } catch {
      // Keep the card's fallback gradient when an image URL is invalid.
    }
  });
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
  $("#highlight-count").textContent = (state.data.highlights || []).length;
  renderOverview();
  renderServerStatus();
  renderRollcalls();
  renderTrainees();
  renderEvents();
  renderWarDiary();
  renderHighlights();
  renderLeaderboards();
  applyMapBackgrounds();
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
    <article class="feature-card latest-result-card"${lastResult.map_image ? ` data-map-image="${escapeHtml(lastResult.map_image)}"` : ""}>
      <span class="card-kicker">LATEST WAR-DIARY RESULT</span><h3>7DR ${escapeHtml(lastResult.score)} ${escapeHtml(lastResult.opponent)}</h3>
      <p>${statsDate(lastResult.date || "Date unavailable", lastResult.stats_url)} · ${escapeHtml(lastResult.map)}</p>
      <span class="result ${escapeHtml(lastResult.outcome)}">${escapeHtml(lastResult.outcome)}</span>
    </article>` : `<article class="feature-card"><span class="card-kicker">LATEST WAR-DIARY RESULT</span><h3>No result recorded</h3><p>The latest submitted match will appear here.</p></article>`;

  const holders = state.data.botr.holders;
  const ratCard = `<article class="feature-card rat-card"><span class="card-kicker">RAT OF THE WEEK</span><h3>${holders.length ? holders.map(escapeHtml).join(" · ") : "Awaiting a winner"}</h3><p>${holders.length ? "Current holder of the Rat Of The Week Discord role." : "No current member has the configured role."}</p></article>`;
  $("#overview-grid").innerHTML = eventCard + resultCard + ratCard;

  const links = [
    ["history", "Historical stats", "Browse previous HLL server matches"],
    ["bifrost", "7DR on Bifrost", "Open the external clan leaderboard"],
    ["kofi", "Support us on Ko-fi", "Help support the 7th Armoured Division"],
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
  const events = state.data.events || [];
  $("#event-grid").innerHTML = events.map(event => `
    <article class="event-card">
      <time datetime="${escapeHtml(event.start_time)}"><strong>${escapeHtml(new Date(event.start_time).toLocaleDateString("en-GB", { day: "2-digit" }))}</strong><span>${escapeHtml(new Date(event.start_time).toLocaleDateString("en-GB", { month: "short" }))}</span></time>
      <div><span class="card-kicker">${event.status === "active" ? "LIVE NOW" : escapeHtml(formatDate(event.start_time))}</span><h3>${escapeHtml(event.name)}</h3><p>${event.location ? escapeHtml(event.location) : "Location TBC"}${event.interested ? ` · ${event.interested} interested` : ""}</p></div>
      ${externalLink(event.url, "Discord")}
    </article>`).join("") || emptyState("No upcoming Discord events are scheduled.");
  $("#event-calendar").innerHTML = renderEventCalendar(events);
  applyEventLayout();
}

function localDateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function renderEventCalendar(events) {
  if (!events.length) return emptyState("No upcoming Discord events are scheduled.");
  const months = new Map();
  events.forEach(event => {
    const date = new Date(event.start_time);
    if (Number.isNaN(date.getTime())) return;
    const monthKey = `${date.getFullYear()}-${date.getMonth()}`;
    if (!months.has(monthKey)) months.set(monthKey, { year: date.getFullYear(), month: date.getMonth(), events: [] });
    months.get(monthKey).events.push({ ...event, date });
  });
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const todayKey = localDateKey(new Date());
  return [...months.values()].map(group => {
    const byDay = new Map();
    group.events.forEach(event => {
      const key = localDateKey(event.date);
      if (!byDay.has(key)) byDay.set(key, []);
      byDay.get(key).push(event);
    });
    const firstDay = new Date(group.year, group.month, 1);
    const leadingBlanks = (firstDay.getDay() + 6) % 7;
    const daysInMonth = new Date(group.year, group.month + 1, 0).getDate();
    const cells = Array.from({ length: leadingBlanks }, () => '<div class="calendar-day outside" aria-hidden="true"></div>');
    for (let day = 1; day <= daysInMonth; day += 1) {
      const date = new Date(group.year, group.month, day);
      const key = localDateKey(date);
      const dayEvents = byDay.get(key) || [];
      const eventLinks = dayEvents.map(event => {
        const url = safeExternalUrl(event.url);
        const label = `<time>${escapeHtml(event.date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }))}</time><span>${escapeHtml(event.name)}</span>`;
        return url
          ? `<a class="calendar-event${event.status === "active" ? " active" : ""}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(event.name)}">${label}</a>`
          : `<div class="calendar-event${event.status === "active" ? " active" : ""}" title="${escapeHtml(event.name)}">${label}</div>`;
      }).join("");
      cells.push(`<div class="calendar-day${key === todayKey ? " today" : ""}${dayEvents.length ? " has-events" : ""}"><span class="calendar-day-number">${day}</span><div class="calendar-day-events">${eventLinks}</div></div>`);
    }
    const trailingBlanks = (7 - ((leadingBlanks + daysInMonth) % 7)) % 7;
    for (let blank = 0; blank < trailingBlanks; blank += 1) cells.push('<div class="calendar-day outside" aria-hidden="true"></div>');
    return `<section class="calendar-month"><h3>${escapeHtml(firstDay.toLocaleDateString("en-GB", { month: "long", year: "numeric" }))}</h3><div class="calendar-grid"><div class="calendar-weekdays">${weekdays.map(day => `<span>${day}</span>`).join("")}</div>${cells.join("")}</div></section>`;
  }).join("") || emptyState("No upcoming Discord events have valid dates.");
}

function applyEventLayout() {
  const calendar = state.eventLayout === "calendar";
  $("#event-grid").hidden = calendar;
  $("#event-calendar").hidden = !calendar;
  $("#event-view-toggle").textContent = calendar ? "Switch to list view" : "Switch to calendar view";
  $("#event-view-toggle").setAttribute("aria-pressed", String(calendar));
}

function opponentTable(rows) {
  if (!rows.length) return emptyState("No recorded results yet.");
  const body = rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${row.played}</td><td>${row.wins}</td><td>${row.losses}</td></tr>`).join("");
  const headers = ["Opponent", "P", "W", "L"];
  return `<div class="table-wrap"><table><thead><tr>${headers.map(header => `<th>${header}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function resultCards(rows) {
  if (!rows.length) return emptyState("No recorded results yet.");
  return `<div class="match-result-grid">${rows.map(row => `
    <article class="match-result-card"${row.map_image ? ` data-map-image="${escapeHtml(row.map_image)}"` : ""}>
      <div class="match-result-content">
        <div class="match-result-top">${statsDate(row.date, row.stats_url)}<span class="result ${escapeHtml(row.outcome)}">${escapeHtml(row.outcome)}</span></div>
        <h4><span>7DR</span> ${escapeHtml(row.score)} ${escapeHtml(row.opponent)}</h4>
        <small>${escapeHtml(row.map)}</small>
      </div>
    </article>`).join("")}</div>`;
}

function renderWarDiary() {
  const diary = state.data.war_diary;
  $("#war-record").textContent = `${diary.summary.wins}W · ${diary.summary.losses}L`;
  $("#war-summary").innerHTML = `
    <div class="stat"><strong>${diary.summary.played}</strong><small>Played</small></div>
    <div class="stat"><strong>${diary.summary.wins}</strong><small>Wins</small></div>
    <div class="stat"><strong>${diary.summary.losses}</strong><small>Losses</small></div>`;
  $("#recent-results").innerHTML = resultCards(diary.recent);
  $("#opponent-records").innerHTML = opponentTable(diary.opponents);
}

function renderHighlights() {
  const posts = state.data.highlights || [];
  $("#highlight-grid").innerHTML = posts.map(post => {
    const author = escapeHtml(post.author || "7DR member");
    const media = (post.media || []).map(item => {
      const url = safeDiscordMediaUrl(item.url);
      if (!url) return "";
      if (item.kind === "video") {
        const quickTime = String(item.content_type || "").toLowerCase().includes("quicktime") || /\.mov(?:$|\?)/i.test(url);
        if (quickTime) return `<div class="video-fallback"><span>QuickTime .MOV video</span><small>This format may not play in your browser.</small><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open original video</a></div>`;
        return `<div class="highlight-video"><video controls preload="metadata" playsinline aria-label="Video shared by ${author}">
          <source src="${escapeHtml(url)}"${item.content_type ? ` type="${escapeHtml(item.content_type)}"` : ""}>
          Your browser cannot play this video.
        </video><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open video directly</a></div>`;
      }
      return `<img src="${escapeHtml(url)}" alt="${escapeHtml(item.filename || `Image shared by ${post.author || "7DR member"}`)}" loading="lazy" decoding="async">`;
    }).join("");
    if (!media) return "";
    const avatar = safeDiscordMediaUrl(post.author_avatar);
    return `<article class="highlight-card">
      <div class="highlight-media ${(post.media || []).length > 1 ? "multiple" : ""}">${media}</div>
      <div class="highlight-copy">
        <div class="highlight-author">${avatar ? `<img src="${escapeHtml(avatar)}" alt="" loading="lazy">` : ""}<strong>${author}</strong><time datetime="${escapeHtml(post.created_at)}">${escapeHtml(formatDate(post.created_at))}</time></div>
        ${post.caption ? `<p>${escapeHtml(post.caption)}</p>` : ""}
        ${externalLink(post.url, "Open in Discord")}
      </div>
    </article>`;
  }).join("") || emptyState("No image or video highlights have been posted yet.");
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

$("#event-view-toggle").addEventListener("click", () => {
  state.eventLayout = state.eventLayout === "calendar" ? "list" : "calendar";
  applyEventLayout();
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
