/*
  app.js
  ------
  Small shared helpers used by every page. Kept deliberately simple:
  a few fetch() wrappers for talking to the Python backend, and a
  couple of functions for remembering who is logged in.

  Note on "login": since this is a learning project with no framework,
  we are not building real server-side sessions. After a successful
  login the user's info is kept in the browser's localStorage. This is
  fine for a class project demo but would need real sessions/hashed
  passwords for anything real - that is mentioned in the README.
*/

const API_BASE = ""; // same server serves the API, so no host needed

// Admins get a login token from the server; send it with every request.
// (For everyone else this is just an empty object.)
function authHeaders() {
  const raw = localStorage.getItem("naija_events_user");
  const user = raw ? JSON.parse(raw) : null;
  return user && user.token ? { "X-Auth-Token": user.token } : {};
}

// Makes user-written text safe to put inside innerHTML (stops <script> tricks)
function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Reads a response. If the server says our login is no longer valid
// (401 - e.g. the server restarted), forget the stored login and send the
// person to the login page, then come back to where they were.
async function readResponse(res, path) {
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/login" && getCurrentUser()) {
      localStorage.removeItem("naija_events_user");
      const here = window.location.pathname.split("/").pop();
      window.location.href = "/pages/login.html" + (here ? "?next=" + encodeURIComponent(here) : "");
    }
    throw new Error(data.error || "Request failed");
  }
  return data;
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path, { headers: authHeaders() });
  return readResponse(res, path);
}

async function apiSend(method, path, body) {
  const res = await fetch(API_BASE + path, {
    method: method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body || {}),
  });
  return readResponse(res, path);
}

const apiPost = (path, body) => apiSend("POST", path, body);
const apiPut = (path, body) => apiSend("PUT", path, body);

async function apiDelete(path) {
  const res = await fetch(API_BASE + path, { method: "DELETE", headers: authHeaders() });
  return readResponse(res, path);
}

function getCurrentUser() {
  const raw = localStorage.getItem("naija_events_user");
  return raw ? JSON.parse(raw) : null;
}

function setCurrentUser(user) {
  localStorage.setItem("naija_events_user", JSON.stringify(user));
}

function logoutUser() {
  // tell the server too, so the login token stops working and the logout is recorded
  try {
    fetch(API_BASE + "/api/logout", { method: "POST", headers: authHeaders(), keepalive: true });
  } catch (e) { /* the local logout below still happens */ }
  localStorage.removeItem("naija_events_user");
  // The homepage lives at the site root ("/"), not inside /pages/.
  // Using an absolute path works from every page. (Before, this used
  // resolvePath("index.html"), which turned into /pages/index.html when
  // clicked from a page inside /pages/ - that file doesn't exist, hence
  // the {"error": "Not found"} message.)
  window.location.href = "/";
}

// works out the right relative path whether we are at the site root
// (index.html) or inside the /pages/ folder
function resolvePath(target) {
  const inPages = window.location.pathname.includes("/pages/");
  return inPages ? target : "pages/" + target;
}

// fills in the nav bar's login/account area depending on session state,
// called at the bottom of every page
function paintNavAccount() {
  const slot = document.getElementById("nav-account-slot");
  if (!slot) return;
  const user = getCurrentUser();

  if (user) {
    slot.innerHTML = `
      <a href="${resolvePath('profile.html')}" class="btn btn-outline btn-small">${escapeHtml(user.full_name.split(" ")[0])}</a>
      <button class="btn btn-primary btn-small" id="nav-logout-btn">Log out</button>
    `;
    document.getElementById("nav-logout-btn").addEventListener("click", logoutUser);
  } else {
    slot.innerHTML = `
      <a href="${resolvePath('login.html')}" class="btn btn-outline btn-small">Log in</a>
      <a href="${resolvePath('register.html')}" class="btn btn-primary btn-small">Sign up</a>
    `;
  }
}

function showMessage(el, text, type) {
  el.textContent = text;
  el.className = "message show " + (type || "success");
}

function formatNaira(amount) {
  const n = Number(amount || 0);
  if (n === 0) return "Free";
  return "₦" + n.toLocaleString("en-NG");
}

/*
  Category theming
  -----------------
  Every event category automatically gets its own colour + pattern, so
  tickets in different categories look visually distinct without anyone
  having to manually design each one. The same category always maps to
  the same theme because the palette index comes from a simple hash of
  the category name, not from anything random.
*/
const CATEGORY_PALETTE = [
  { bg: "#1E4B36", accent: "#BD8628", pattern: "stripes" },  // forest + gold
  { bg: "#2D3B63", accent: "#9DB4E8", pattern: "dots" },     // indigo
  { bg: "#843C1F", accent: "#D4A155", pattern: "diagonal" }, // brick + sand
  { bg: "#5C3B70", accent: "#E3B7F5", pattern: "zigzag" },   // plum
  { bg: "#1B6B5C", accent: "#8FE3CF", pattern: "waves" },    // teal
  { bg: "#6E2A2A", accent: "#D98A72", pattern: "circles" },  // rust red
  { bg: "#3D5A1E", accent: "#C7E28A", pattern: "grid" },     // olive
  { bg: "#553210", accent: "#C58F43", pattern: "confetti" }, // brown + amber
];

function categoryTheme(category) {
  const text = category || "General";
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    hash = text.charCodeAt(i) + ((hash << 5) - hash);
  }
  const index = Math.abs(hash) % CATEGORY_PALETTE.length;
  return CATEGORY_PALETTE[index];
}

// Applies a category's theme (colour + pattern) onto an element, so the
// same visual identity shows up automatically anywhere a ticket or event
// card for that category is rendered.
function applyCategoryTheme(el, category, shapeClass) {
  const theme = categoryTheme(category);
  el.style.setProperty("--ticket-bg", theme.bg);
  el.style.setProperty("--ticket-accent", theme.accent);
  el.classList.add(shapeClass || "themed-stub", "pattern-" + theme.pattern);
  return theme;
}

function formatDate(isoDate) {
  const d = new Date(isoDate + "T00:00:00");
  return d.toLocaleDateString("en-NG", { day: "numeric", month: "short", year: "numeric" });
}

// Shows or hides nav links that only make sense for organizers
// (Create Event, Dashboard), based on the logged-in user's role.
function paintOrganizerNav() {
  const user = getCurrentUser();
  const isOrganizer = !!(user && user.role === "organizer");
  document.querySelectorAll(".nav-organizer-only").forEach((el) => {
    el.style.display = (user && !isOrganizer) ? "none" : "";
  });
}

// Small badge for event cards: only verified or flagged events get one
function trustBadge(status) {
  if (status === "verified") return '<span class="trust-badge trust-verified">✔ Verified</span>';
  if (status === "flagged") return '<span class="trust-badge trust-scam">⚠ Flagged</span>';
  return "";
}

// Adds an "Admin" link to the nav bar, but only for admin accounts
function paintAdminNav() {
  const user = getCurrentUser();
  const list = document.querySelector(".nav-links");
  if (!user || user.role !== "admin" || !list) return;
  list.insertAdjacentHTML("beforeend",
    `<li><a href="${resolvePath('admin.html')}">Admin</a></li>`);
}

document.addEventListener("DOMContentLoaded", paintNavAccount);
document.addEventListener("DOMContentLoaded", paintAdminNav);
document.addEventListener("DOMContentLoaded", paintOrganizerNav);
