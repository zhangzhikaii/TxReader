// Loaded last: page switching by #hash, logout, and start-up.

const VIEWS = ["add", "transactions", "monthly", "balances", "export", "settings", "security"];
function route() {
  let view = location.hash.slice(1);
  if (view === "recognize") { view = "add"; showMethod("screenshot"); } // old bookmark
  if (view === "rates" || view === "settings-email") { // rates moved into settings; old links still work
    showSetTab(view === "rates" ? "rates" : "email");
    view = "settings";
    history.replaceState(null, "", "#settings");
  }
  if (!VIEWS.includes(view)) view = "add";
  for (const v of VIEWS) $("view-" + v).hidden = v !== view;
  for (const a of document.querySelectorAll("nav a")) a.classList.toggle("active", a.dataset.view === view);
  showImage(null);
  if (view === "add") { if (method !== "manual") loadPending(); if (method === "email") loadEmailState(); loadSuggestions(); }
  if (view === "transactions") loadSuggestions().then(fillTxFilters).then(loadTransactions); // lists may have changed in settings
  if (view === "export") { loadExportRows(); loadExportHistory(); }
  if (view === "settings") { loadSettings(); loadRates(); loadEmailSettings(); }
  if (view === "balances") loadBalances($("bal-month").value || null);
  if (view === "monthly") loadMonthly($("mo-month").value || null);
  if (view === "security") loadSecurity();
}
window.addEventListener("hashchange", route);

$("logout").onclick = async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
};

// Show the logout button only when login is on
api("/api/security").then(d => { $("logout").hidden = !d.auth; }).catch(() => {});
renderManual();
showMethod(method);
route();
