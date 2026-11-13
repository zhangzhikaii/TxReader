// Loaded last: page switching by #hash, logout, and start-up.

const VIEWS = ["add", "transactions", "monthly", "balances", "holdings", "settings", "security"];
function route() {
  let view = location.hash.slice(1);
  // Leaving settings with unsaved edits: asked first; staying puts the address back
  if (!$("view-settings").hidden && !["settings", "rates", "settings-email"].includes(view) && !leaveSettings()) {
    history.replaceState(null, "", "#settings");
    return;
  }
  if (!$("view-transactions").hidden && view !== "transactions" && !leaveTxEdit()) { // same for transactions
    history.replaceState(null, "", "#transactions");
    return;
  }
  if (!$("view-holdings").hidden && view !== "holdings" && !leaveHdEdit()) { // and for 持仓
    history.replaceState(null, "", "#holdings");
    return;
  }
  if (view === "recognize") { view = "add"; showMethod("screenshot"); } // old bookmark
  if (view === "rates" || view === "settings-email") { // rates moved into settings; old links still work
    showSetTab(view === "rates" ? "rates" : "email");
    view = "settings";
    history.replaceState(null, "", "#settings");
  }
  if (view === "export") view = "transactions"; // the export page became a button there
  if (!VIEWS.includes(view)) view = "add";
  for (const v of VIEWS) $("view-" + v).hidden = v !== view;
  for (const a of document.querySelectorAll("nav a")) a.classList.toggle("active", a.dataset.view === view);
  showImage(null);
  if (view === "add") { if (method !== "manual") loadPending(); if (method === "email") loadEmailState(); loadSuggestions(); }
  if (view === "transactions") loadSuggestions().then(fillTxFilters).then(loadTransactions); // lists may have changed in settings
  if (view === "settings") { loadSettings(); loadEmailSettings(); }
  if (view === "balances") loadBalances($("bal-month").value || null);
  if (view === "holdings") { hdSynced.clear(); const p = hdPreset; hdPreset = null; p ? loadHoldings(p.account, p.week) : loadHoldings(); }
  if (view === "monthly") loadMonthlyPage();
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
showSetTab(load("tx.settings.tab"));
route();
