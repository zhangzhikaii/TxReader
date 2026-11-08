// 「记一笔 › 手动输入」, and the account / currency / category suggestions other pages use too.

// The lists from the settings page, in their order: accounts (each with its currencies, most
// used first), currencies, categories
let sugg = { accounts: [], currencies: [], categories: [] };
let accountCurrencies = new Map(); // account name -> [currency, ...]
let knownCurrencies = new Set(), knownCategories = new Set();
const optionList = list => [...new Set(list)].map(v => `<option value="${esc(v)}">`).join("");
async function loadSuggestions() {
  try { sugg = await api("/api/suggestions"); } catch { return; }
  accountCurrencies = new Map(sugg.accounts.map(a => [a.name, a.currencies]));
  knownCurrencies = new Set(sugg.currencies);
  knownCategories = new Set(sugg.categories);
  $("m-accounts").innerHTML = optionList(accountCurrencies.keys());
  $("m-categories").innerHTML = optionList(sugg.categories);
  currencyLists(); // not a full re-render, which would disturb a cell being typed in
}
// Each row offers its account's currencies first, then the rest
function currencyLists() {
  $("m-cur-lists").innerHTML = mRows.map(r =>
    `<datalist id="m-cur-${r.key}">${optionList([...(accountCurrencies.get(r.account.trim()) ?? []), ...sugg.currencies])}</datalist>`).join("");
}
// What these rows would add to the lists (same rules as the server's lists.unknown)
function unknownNames(rows) {
  const out = { accounts: new Map(), currencies: new Set(), pairs: [], categories: new Set() };
  for (const { account: a, currency: c, category: cat } of rows) {
    const known = accountCurrencies.get(a);
    if (!known) { if (!out.accounts.has(a)) out.accounts.set(a, new Set()); out.accounts.get(a).add(c); }
    else if (!known.includes(c) && !out.pairs.some(([x, y]) => x === a && y === c)) out.pairs.push([a, c]);
    if (!knownCurrencies.has(c)) out.currencies.add(c);
    if (!knownCategories.has(cat)) out.categories.add(cat);
  }
  return out;
}

// A row: { key, dt ("2026-11-06T14:30:05", as <input type=datetime-local step=1> uses), account, currency,
//          sign (-1 expense / 1 income), amount (text as typed), category, description }
const nowLocal = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
let mKey = 1;
function newManualRow(prev) {
  const last = prev ?? JSON.parse(load("tx.manual.last") || "null") ?? {};
  return { key: mKey++, dt: prev ? prev.dt : nowLocal(), account: last.account ?? "", currency: last.currency ?? "",
           sign: -1, amount: "", category: last.category ?? "", description: "" };
}
let mRows = (() => {
  try {
    const saved = JSON.parse(load("tx.manual.draft") || "[]");
    if (saved.length) return saved.map(r => ({ ...r, key: mKey++ }));
  } catch {}
  return [newManualRow()];
})();
const saveManualDraft = () => save("tx.manual.draft", JSON.stringify(mRows.filter(r => r.amount || r.description)));

function renderManual() {
  const active = document.activeElement?.dataset ?? {};
  $("m-rows").innerHTML = mRows.map(r => `<tr class="${r.sign > 0 ? "income" : ""}">
    <td class="dt"><input type="datetime-local" step="1" data-m="${r.key}" data-k="dt" value="${esc(r.dt)}"></td>
    <td><input data-m="${r.key}" data-k="account" value="${esc(r.account)}" list="m-accounts"></td>
    <td><input data-m="${r.key}" data-k="currency" value="${esc(r.currency)}" list="m-cur-${r.key}"></td>
    <td><button class="sign ${r.sign > 0 ? "in" : ""}" data-sign="${r.key}" title="点击切换支出 / 收入">${r.sign > 0 ? "收入" : "支出"}</button></td>
    <td class="amt"><input data-m="${r.key}" data-k="amount" value="${esc(r.amount)}" inputmode="decimal" placeholder="0.00"></td>
    <td><input data-m="${r.key}" data-k="category" value="${esc(r.category)}" list="m-categories"></td>
    <td><input data-m="${r.key}" data-k="description" value="${esc(r.description)}"></td>
    <td><button class="icon" data-m-del="${r.key}" title="删除这一行" tabindex="-1">✕</button></td></tr>`).join("");
  currencyLists();
  const n = mRows.filter(r => r.amount.trim()).length;
  $("m-save").textContent = `入库（${n} 条）`;
  $("m-save").disabled = n === 0;
  if (active.m && active.k) $("m-rows").querySelector(`[data-m="${active.m}"][data-k="${active.k}"]`)?.focus();
}
const mById = key => mRows.find(r => r.key === +key);
function focusManual(key, k = "amount") {
  const r = key ? mById(key) : mRows.find(r => !r.amount) ?? mRows[mRows.length - 1];
  if (r) $("m-rows").querySelector(`[data-m="${r.key}"][data-k="${k}"]`)?.focus();
}
function addManualRow() {
  const r = newManualRow(mRows[mRows.length - 1]);
  mRows.push(r);
  renderManual();
  return r;
}

$("m-rows").addEventListener("input", e => {
  const { m, k } = e.target.dataset;
  const r = m && mById(m);
  if (!r) return;
  r[k] = e.target.value;
  if (k === "amount") {
    // A leading + or - sets the sign
    const sign = r.amount.trim()[0];
    if (sign === "+" || sign === "-") {
      r.sign = sign === "+" ? 1 : -1;
      r.amount = e.target.value = r.amount.trim().slice(1);
      const tr = e.target.closest("tr");
      tr.classList.toggle("income", r.sign > 0);
      const b = tr.querySelector("[data-sign]");
      b.classList.toggle("in", r.sign > 0);
      b.textContent = r.sign > 0 ? "收入" : "支出";
    }
    const n = mRows.filter(r => r.amount.trim()).length;
    $("m-save").textContent = `入库（${n} 条）`;
    $("m-save").disabled = n === 0;
  }
  e.target.classList.remove("invalid");
  saveManualDraft();
});
$("m-rows").addEventListener("change", e => {
  const { m, k } = e.target.dataset;
  const r = m && mById(m);
  if (!r || k !== "account") return;
  // The currency follows the account: kept if the account is used with it, else its most used one
  const curs = accountCurrencies.get(r.account.trim());
  if (curs?.length && !curs.includes(r.currency.trim())) r.currency = curs[0];
  renderManual();
  saveManualDraft();
});
$("m-rows").addEventListener("click", e => {
  const sign = e.target.dataset.sign;
  if (sign) { const r = mById(sign); r.sign = -r.sign; renderManual(); saveManualDraft(); return; }
  const del = e.target.dataset.mDel;
  if (del) {
    mRows = mRows.filter(r => r.key !== +del);
    if (!mRows.length) mRows.push(newManualRow());
    renderManual();
    saveManualDraft();
  }
});
// Enter: same column, next row (Shift+Enter: previous); on the last row, add one
tableKeys($("m-rows"), el => el.dataset.k ? `input[data-k="${el.dataset.k}"]` : null,
  (el, e) => { if (!e.shiftKey) focusManual(addManualRow().key, el.dataset.k); });
$("m-add").onclick = () => focusManual(addManualRow().key);

$("m-save").onclick = async () => {
  const todo = mRows.filter(r => r.amount.trim());
  let bad = null;
  const rows = todo.map(r => {
    const amount = parseAmount(r.amount);
    const dt = r.dt && parseDate(r.dt.replace("T", " "));
    const missing = !dt ? "dt" : !amount ? "amount" : ["account", "currency", "category"].find(k => !r[k].trim());
    if (missing) bad ??= { r, k: missing };
    return { date: dt?.date, account: r.account.trim(), currency: r.currency.trim(),
             amount: r.sign * Math.abs(amount), category: r.category.trim(), description: r.description.trim() };
  });
  if (bad) {
    const el = $("m-rows").querySelector(`[data-m="${bad.r.key}"][data-k="${bad.k}"]`);
    el?.classList.add("invalid");
    el?.focus();
    return flash("m-status", { dt: "请填写日期时间", amount: `金额不对：「${bad.r.amount}」`,
      account: "请填写账户", currency: "请填写币种", category: "请填写种类" }[bad.k]);
  }
  flash("m-status", "");
  openConfirm(todo, rows);
};

let pendingSave = null; // { todo, rows, add } while the dialog is open
function openConfirm(todo, rows) {
  const unknown = unknownNames(rows);
  const add = unknown.accounts.size + unknown.currencies.size + unknown.pairs.length + unknown.categories.size > 0;
  pendingSave = { todo, rows, add };
  $("m-confirm-title").textContent = `确认入库 ${rows.length} 笔`;
  const items = [
    ...[...unknown.accounts].map(([a, cs]) => `新账户「${esc(a)}」（币种：${[...cs].map(esc).join("、")}）`),
    ...unknown.pairs.map(([a, c]) => `账户「${esc(a)}」新增币种「${esc(c)}」`),
    ...[...unknown.currencies].map(c => `新币种「${esc(c)}」`),
    ...[...unknown.categories].map(c => `新种类「${esc(c)}」`),
  ];
  $("m-confirm-new").hidden = !add;
  $("m-confirm-new").innerHTML = add ? `下面这些还不在「设置」的清单里，确认后会一起新增：<ul>${items.map(i => `<li>${i}</li>`).join("")}</ul>如果是填错了，请点「返回修改」。` : "";
  const sorted = [...rows].sort((a, b) => a.date.localeCompare(b.date));
  $("m-confirm-rows").innerHTML = sorted.map(t => `<tr class="${t.amount > 0 ? "income" : ""}">
    <td>${esc(t.date)}</td><td>${esc(parseDate(t.date).weekday)}</td><td>${esc(t.account)}</td><td>${esc(t.currency)}</td>
    <td class="num">${fmtAmount(t.amount)}</td><td>${esc(t.category)}</td><td class="wrap">${esc(t.description)}</td></tr>`).join("");
  // Totals per currency, so a slipped digit stands out
  const totals = new Map();
  for (const t of rows) {
    const x = totals.get(t.currency) ?? { out: 0, in: 0 };
    x[t.amount < 0 ? "out" : "in"] += t.amount;
    totals.set(t.currency, x);
  }
  $("m-confirm-totals").innerHTML = "合计：" + [...totals].map(([c, x]) =>
    `${esc(c)} ${[x.out && `支出 ${fmtAmount(-x.out)}`, x.in && `收入 ${fmtAmount(x.in)}`].filter(Boolean).join("，")}`).join("；");
  $("m-confirm-ok").textContent = add ? `新增并入库 ${rows.length} 笔` : `确认入库 ${rows.length} 笔`;
  $("m-confirm-ok").disabled = false;
  $("m-confirm").showModal();
  $("m-confirm-cancel").focus(); // a stray Enter goes back rather than storing
}
$("m-confirm-cancel").onclick = () => $("m-confirm").close();
$("m-confirm").addEventListener("close", () => { pendingSave = null; });
$("m-confirm-ok").onclick = async () => {
  if (!pendingSave) return;
  const { todo, rows, add } = pendingSave;
  $("m-confirm-ok").disabled = true;
  try {
    await api("/api/transactions", send("POST", { rows, add_accounts: add }));
    $("m-confirm").close();
    const last = todo[todo.length - 1];
    save("tx.manual.last", JSON.stringify({ account: last.account, currency: last.currency, category: last.category }));
    showRecent(rows);
    mRows = mRows.filter(r => !r.amount.trim()); // rows without an amount stay
    if (!mRows.length) mRows.push(newManualRow());
    saveManualDraft();
    renderManual();
    flash("m-status", `已入库 ${rows.length} 条`);
    loadSuggestions();
    focusManual();
  } catch (e) {
    $("m-confirm").close();
    flash("m-status", "入库失败：" + e.message);
    loadSuggestions(); // the lists may have changed elsewhere
  }
};

let recent = [];
function showRecent(rows) {
  recent = rows.map(r => ({ ...r, weekday: parseDate(r.date).weekday })).concat(recent).slice(0, 50);
  $("m-recent").hidden = false;
  $("m-recent-rows").innerHTML = recent.map(t => `<tr class="${t.amount > 0 ? "income" : ""}">
    <td>${esc(t.date)}</td><td>${esc(t.weekday)}</td><td>${esc(t.account)}</td><td>${esc(t.currency)}</td>
    <td class="num">${fmtAmount(t.amount)}</td><td>${esc(t.category)}</td><td class="wrap">${esc(t.description)}</td></tr>`).join("");
}
