// ---------- CONFIG ----------
const metaApi = document.querySelector('meta[name="api-base"]');
const API_BASE =
  new URLSearchParams(location.search).get("api") ||
  (metaApi ? metaApi.content : "http://127.0.0.1:8000");

document.getElementById("apiLabel").textContent = "API: " + API_BASE;

// ---------- STATE ----------
let incidents = [];
let poller = null;

// ---------- HELPERS ----------
const el = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));

// ---------- RENDER ----------
function render() {
  const list = el("list");
  const q = el("q").value.trim().toLowerCase();
  const type = el("type").value;
  const onlyHigh = el("onlyHigh").checked;

  const filtered = incidents.filter((it) => {
    const hit =
      !q ||
      (it.summary || "").toLowerCase().includes(q) ||
      (it.location || "").toLowerCase().includes(q) ||
      (it.emergency_type || "").toLowerCase().includes(q);
    const typeOK = type === "all" || it.emergency_type === type;
    const sevOK = !onlyHigh || it.severity === "high";
    return hit && typeOK && sevOK;
  });

  el("empty").style.display = filtered.length ? "none" : "block";

  list.innerHTML = filtered
    .map(
      (it) => `
      <div class="card">
        <h3 class="title">${escapeHtml(it.summary || "(no summary)")}</h3>
        <div class="badges">
          <span class="badge">Type: ${escapeHtml(it.emergency_type || "other")}</span>
          <span class="badge">People: ${escapeHtml(String(it.people_involved ?? "?"))}</span>
          <span class="badge sev">Severity: ${(it.severity || "medium").toUpperCase()}</span>
        </div>
        <p><strong>Location:</strong> ${escapeHtml(it.location || "unknown")}</p>
        <div class="meta">ID: ${it.id ?? "?"}</div>
      </div>
    `
    )
    .join("");
}

// ---------- DATA ----------
async function fetchIncidents() {
  try {
    el("loading").style.display = "inline";
    el("error").style.display = "none";
    const res = await fetch(API_BASE + "/incidents", { cache: "no-store" });
    if (!res.ok) throw new Error("Fetch failed " + res.status);
    const data = await res.json();
    incidents = [...data].reverse(); // show newest first
    render();
  } catch (e) {
    el("error").style.display = "block";
    el("error").textContent = "Error: " + (e.message || e);
  } finally {
    el("loading").style.display = "none";
  }
}

async function addSample() {
  try {
    el("loading").style.display = "inline";
    await fetch(API_BASE + "/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transcript: "There is a fire at 123 Main Street. Two people are inside.",
      }),
    });
    await fetchIncidents();
  } catch (e) {
    el("error").style.display = "block";
    el("error").textContent = "Error: " + (e.message || e);
  } finally {
    el("loading").style.display = "none";
  }
}

// ---------- WIRING ----------
el("q").addEventListener("input", render);
el("type").addEventListener("change", render);
el("onlyHigh").addEventListener("change", render);
el("refreshBtn").addEventListener("click", fetchIncidents);
el("sampleBtn").addEventListener("click", addSample);

// Initial load + poll every 4s
fetchIncidents();
poller = setInterval(fetchIncidents, 4000);
