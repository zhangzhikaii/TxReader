// 「持仓」: what each investment account holds, one snapshot a week, its market value against the
// balance sheet's balance (the cost). Read-only until 「✎ 修改」; 「保存」 saves the whole week at once.
// Securities with a Yahoo Finance symbol are priced from there (see portfolio.py): opening an account
// syncs it once per visit, 「更新价格」 fetches this week's again, 🔍 looks a code up.

let hd = null;      // the server's page (GET /api/holdings)
let hdEdit = null;  // while editing: { currency, rows: [{ kind, name, code, currency, quantity, price, unit_cost, amount,
                    //   symbol, price_source, price_at, lookup (new: looked up before saving) }] as typed }
let hdSynced = new Set(); // accounts whose prices were synced this visit (main.js clears it)
let hdPriceMsg = "";      // what the last price fetch said
let hdLockMsg = "";
let hdPreset = null; // { account, week }: set by the balance sheet's links, used by the next visit
const HD_KINDS = { security: "证券", cash: "现金" };

// Weeks are named by their Monday, "2026-11-09"
const hdDate = w => new Date(+w.slice(0, 4), +w.slice(5, 7) - 1, +w.slice(8, 10));
const hdShift = (w, days) => { const d = hdDate(w); d.setDate(d.getDate() + days); return ymd(d); };
const hdWeekLabel = w => {
  const a = hdDate(w), b = hdDate(hdShift(w, 6));
  return `${a.getFullYear()}/${pad(a.getMonth() + 1)}/${pad(a.getDate())} – ${pad(b.getMonth() + 1)}/${pad(b.getDate())}`;
};
const hdShort = w => `${+w.slice(5, 7)}/${+w.slice(8, 10)}`;
const fmtQty = n => n.toLocaleString("en-US", { maximumFractionDigits: 6 });
const fmtRatio = r => (r > 0 ? "+" : "") + (r * 100).toFixed(2) + "%";
const hdSigned = n => (n > 0 ? "+" : "") + fmtAmount(n);
// "" and anything not a number: NaN
const hdNum = t => { const s = String(t ?? "").replace(/[,\s]/g, ""); return s === "" ? NaN : Number(s); };

async function loadHoldings(accountId = hd?.account ?? (+load("tx.holdings.account") || null), week = hd?.week) {
  const q = new URLSearchParams();
  if (accountId != null) q.set("account_id", accountId);
  if (week) q.set("week", week);
  try {
    hd = await api("/api/holdings?" + q);
  } catch (e) {
    if (accountId != null) return loadHoldings(null, week); // that account is gone or no longer 投资
    return flash("hd-status", "读取失败：" + e.message);
  }
  flash("hd-status", hdPriceMsg);
  if (hd.account != null) save("tx.holdings.account", String(hd.account));
  renderHoldings();
  if (hd.account != null && !hdEdit && !hdSynced.has(hd.account)) hdSync(hd.account);
}
// Ended weeks' closes, the snapshot carried into the weeks since, stale live prices (server side)
async function hdSync(account) {
  hdSynced.add(account);
  hdPriceMsg = "";
  flash("hd-status", "正在从 Yahoo Finance 取价格…");
  let r;
  try {
    r = await api("/api/holdings/sync", send("POST", { account_id: account }));
  } catch (e) {
    hdPriceMsg = "取价格失败：" + e.message;
    return flash("hd-status", hdPriceMsg);
  }
  hdPriceMsg = r.warnings.length ? "有些价格没取到：" + r.warnings.join("；") : "";
  if (r.changed && hd?.account === account && !hdEdit) await loadHoldings();
  else flash("hd-status", hdPriceMsg);
}
$("hd-refresh").onclick = async () => {
  $("hd-refresh").disabled = true;
  flash("hd-status", "正在更新价格…");
  try {
    const r = await api("/api/holdings/refresh", send("POST", { account_id: hd.account }));
    hdPriceMsg = (r.updated ? `更新了 ${r.updated} 个价格` : "没有可更新的价格") +
      (r.warnings.length ? "；没取到：" + r.warnings.join("；") : "");
  } catch (e) {
    hdPriceMsg = "更新失败：" + e.message;
  }
  await loadHoldings();
};
// Switching account or week while editing: asked first
function hdGo(accountId, week) {
  if (!leaveHdEdit()) return renderHoldings(); // puts the selects back
  loadHoldings(accountId, week);
}
$("hd-account").addEventListener("change", e => hdGo(+e.target.value, hd.week));
$("hd-prev").onclick = () => hdGo(hd.account, hdShift(hd.week, -7));
$("hd-next").onclick = () => hdGo(hd.account, hdShift(hd.week, 7));
$("hd-today").onclick = () => hdGo(hd.account, hd.this_week);
$("hd-history").addEventListener("click", e => { const tr = e.target.closest("tr[data-week]"); if (tr) hdGo(hd.account, tr.dataset.week); });

const hdAccount = () => hd.accounts.find(a => a.id === hd.account);
function renderHoldings() {
  const none = !hd.accounts.length;
  $("hd-warn").innerHTML = none
    ? `<div class="warn-box">还没有投资账户。去「<a href="#settings">设置 › 账户</a>」点「修改」，把账户的类型设为「投资」。</div>` : "";
  for (const id of ["hd-tiles", "hd-card", "hd-history-card"]) $(id).hidden = none;
  $("hd-account").innerHTML = hd.accounts.map(a => `<option value="${a.id}" ${a.id === hd.account ? "selected" : ""}>${esc(a.name)}</option>`).join("");
  $("hd-week").textContent = hdWeekLabel(hd.week);
  $("hd-next").disabled = hd.week >= hd.this_week;
  $("hd-today").disabled = hd.week === hd.this_week;
  $("hd-refresh").hidden = none || hd.week !== hd.this_week;
  $("hd-refresh").disabled = !!hdEdit || !hd.rows.some(r => r.symbol && r.price_source === "live");
  if (none) return;
  renderHdTiles();
  renderHdRows();
  renderHdHistory();
  renderHdLock();
}

function renderHdTiles() {
  const v = hd.valuation, cur = esc(v.currency ?? ""); // as saved, until the edits are
  const notes = v.notes.length ? `<div class="warn-box">${v.notes.map(esc).join("；")}</div>` : "";
  const money = x => x == null ? `<span class="missing">—</span>` : `${fmtAmount(x)}<span class="unit">${cur}</span>`;
  // Two gains: against the balance sheet's balance (everything, cash and realized gains included)
  // and against the unit costs typed in (the securities that have one)
  const part = (name, amount, gain, ratio, extra = "") => `<div class="gain-line"><span class="name">${name}</span>` +
    `<span class="part">成本 ${amount == null ? "—" : fmtAmount(amount)}</span>` +
    `<span class="part">盈亏 ${gain == null ? "—" : `<span class="${amtClass(gain)}">${hdSigned(gain)}</span>`}` +
    `${ratio == null ? "" : `（<span class="${amtClass(ratio)}">${fmtRatio(ratio)}</span>）`}</span>${extra}</div>`;
  const uncosted = v.uncosted ? `<span class="muted">${v.uncosted} 个证券没填成本价，不算在内</span>` : "";
  $("hd-tiles").innerHTML = notes + `<div class="hero">
    <div class="label">市值 · ${hdWeekLabel(hd.week)}${hd.filled ? "" : "（这周还没填）"}</div>
    <div class="row"><span class="total">${money(v.market_value)}</span></div>
    <div class="split gains">${part("较结余", v.cost, v.gain, v.ratio, `<span class="muted">成本 = 结余页这周周日的余额，市值含现金</span>`)}
      ${part("持仓盈亏", v.position_cost, v.position_gain, v.position_ratio, uncosted || `<span class="muted">按各证券的成本价，不含现金</span>`)}</div>
  </div>`;
}

// The table's columns; the order and the sort are per-browser choices (localStorage), not locked.
// view: only while read-only. sort: the number a security sorts by (null last), given (row, valuation row).
const HD_COLS = [
  { key: "kind", label: "类型" },
  { key: "name", label: "名称" },
  { key: "code", label: "代码" },
  { key: "currency", label: "币种" },
  { key: "quantity", label: "数量", sort: r => r.quantity },
  { key: "price", label: "现价", sort: r => r.price },
  { key: "unit_cost", label: "成本价", sort: r => r.unit_cost },
  { key: "amount", label: "金额", sort: (r, val) => val.value },
  { key: "converted", label: "折算", view: true, sort: (r, val) => val.converted },
  { key: "share", label: "占比", view: true, sort: (r, val) => hdShare(val) },
  { key: "own_gain", label: "持仓盈亏（原币）", view: true, sort: (r, val) => val.own_gain },
  { key: "gain", label: "持仓盈亏（折算）", view: true, sort: (r, val) => val.gain },
];
// A row's part of the account's market value (cash included); null without a total
const hdShare = val => { const t = hd.valuation.market_value;
  return val.converted == null || !t ? null : val.converted / t; };
const hdColDef = k => HD_COLS.find(c => c.key === k);
let hdOrder = (() => { // saved keys that still exist; a column added since goes after its default neighbour
  let saved = [];
  try { saved = JSON.parse(load("tx.holdings.cols")) || []; } catch {}
  const keys = [...new Set(saved.filter(k => hdColDef(k)))];
  HD_COLS.forEach((c, i) => {
    if (keys.includes(c.key)) return;
    const at = i ? keys.indexOf(HD_COLS[i - 1].key) : -1;
    keys.splice(at + 1, 0, c.key);
  });
  return keys;
})();
let hdSort = (() => { try { const s = JSON.parse(load("tx.holdings.sort")); return hdColDef(s?.key)?.sort ? s : null; } catch { return null; } })();
let hdColsOpen = false;
const hdCols = () => hdOrder.map(hdColDef).filter(c => hdEdit ? !c.view : true);

// Read-only rows in display order: with a sort, the securities by that column (no value last,
// ties as saved), then the cash as saved
function hdViewOrder() {
  const idx = hd.rows.map((_, i) => i);
  if (!hdSort) return idx;
  const by = hdColDef(hdSort.key).sort, dir = hdSort.dir === "asc" ? 1 : -1;
  const key = i => { const x = by(hd.rows[i], hd.valuation.rows[i]); return x == null || Number.isNaN(x) ? null : x; };
  const sec = idx.filter(i => hd.rows[i].kind === "security"), cash = idx.filter(i => hd.rows[i].kind !== "security");
  sec.sort((a, b) => {
    const x = key(a), y = key(b);
    if (x === y) return a - b;
    if (x === null) return 1;
    if (y === null) return -1;
    return (x - y) * dir || a - b;
  });
  return [...sec, ...cash];
}

function hdViewCell(key, r, val) {
  const sec = r.kind === "security", none = `<td class="none">—</td>`;
  switch (key) {
    case "kind": return `<td>${HD_KINDS[r.kind]}</td>`;
    case "name": case "currency": return `<td>${esc(r[key])}</td>`;
    case "code": return `<td>${esc(r.code)}${r.symbol && r.symbol !== r.code ? `<span class="price-note">${esc(r.symbol)}</span>` : ""}</td>`;
    case "quantity": return sec ? `<td class="num">${fmtQty(r.quantity)}</td>` : none;
    case "price": return sec ? `<td class="num">${fmtQty(r.price)}${r.symbol ? `<span class="price-note">${hdPriceNote(r)}</span>` : ""}</td>` : none;
    case "unit_cost": return !sec || r.unit_cost == null ? none : `<td class="num">${fmtQty(r.unit_cost)}</td>`;
    case "amount": return moneyCell(val.value);
    case "converted": return moneyCell(val.converted);
    case "share": { const x = hdShare(val); return x == null ? none : `<td class="num">${(x * 100).toFixed(2)}%</td>`; }
    case "own_gain": return !sec || val.own_gain == null ? none
      : `<td class="num ${amtClass(val.own_gain)}">${hdSigned(val.own_gain)}<span class="unit">${esc(r.currency)}</span>` +
        `${val.own_ratio == null ? "" : `<span class="pct">${fmtRatio(val.own_ratio)}</span>`}</td>`;
    case "gain": return !sec || val.cost == null ? none
      : val.gain == null ? `<td class="missing">缺汇率</td>`
      : `<td class="num ${amtClass(val.gain)}">${hdSigned(val.gain)}${val.ratio == null ? "" : `<span class="pct">${fmtRatio(val.ratio)}</span>`}</td>`;
  }
}
function hdEditCell(key, r, i, curSel) {
  const sec = r.kind === "security", none = `<td class="none">—</td>`;
  const inp = (k, cls = "") =>
    `<input data-hd="${i}" data-k="${k}" value="${esc(r[k])}" class="${cls}"${["quantity", "price", "unit_cost", "amount"].includes(k) ? ' inputmode="decimal"' : ""}${k === "unit_cost" ? ' placeholder="可不填"' : ""}>`;
  const bad = k => Number.isNaN(hdNum(r[k])) ? "invalid" : "";
  switch (key) {
    case "kind": return `<td>${HD_KINDS[r.kind]}</td>`;
    case "name": return `<td>${inp("name")}</td>`;
    case "code": return `<td>${sec ? `<div class="hd-code">${inp("code")}<button class="icon" data-hd-find="${i}" title="在 Yahoo Finance 查找行情">${HD_SEARCH_ICON}</button></div>` +
      (r.symbol ? `<span class="price-note">行情 ${esc(r.symbol)} · <button class="hd-link" data-hd-unlink="${i}" title="不再自动取价格">不用</button></span>` : "")
      : inp("code")}</td>`;
    case "currency": return `<td>${curSel(r.currency, `data-hd="${i}" data-k="currency"`)}</td>`;
    case "quantity": return sec ? `<td class="num">${inp("quantity", bad("quantity"))}</td>` : none;
    case "price": return sec ? `<td class="num">${inp("price", r.lookup && !r.symbol && r.price.trim() === "" ? "" : bad("price"))}` +
      `${r.symbol ? `<span class="price-note">${hdEditPriceNote(r, i)}</span>` : ""}</td>` : none;
    case "unit_cost": return sec ? `<td class="num">${inp("unit_cost", String(r.unit_cost).trim() !== "" && !(hdNum(r.unit_cost) >= 0) ? "invalid" : "")}</td>` : none;
    case "amount": {
      if (!sec) return `<td class="num">${inp("amount", bad("amount"))}</td>`;
      const own = hdNum(r.quantity) * hdNum(r.price);
      return `<td class="num" data-hd-own>${Number.isNaN(own) ? "" : fmtAmount(own)}</td>`;
    }
  }
}

// Under a price: where it came from
const hdPriceNote = r => {
  if (r.price_source === "manual") return "手填";
  if (!r.price_at) return "还没取到";
  if (r.price_source === "close") return `${+r.price_at.slice(5, 7)}/${+r.price_at.slice(8, 10)} 收盘`;
  const d = new Date(r.price_at);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())} 最新`;
};
const hdEditPriceNote = (r, i) => r.price_source === "manual"
  ? `手填 · <button class="hd-link" data-hd-auto="${i}" title="从 Yahoo Finance 取这周的价格">改回自动</button>`
  : "自动 · " + hdPriceNote(r);
const HD_SEARCH_ICON = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2">` +
  `<circle cx="7" cy="7" r="5"/><path d="m11 11 3.5 3.5" stroke-linecap="round"/></svg>`;

function renderHdColBar() {
  $("hd-cols-toggle").textContent = hdColsOpen ? "完成" : "⇄ 列顺序";
  const box = $("hd-cols");
  box.hidden = !hdColsOpen;
  if (!hdColsOpen) return;
  const last = hdOrder.length - 1;
  box.innerHTML = hdOrder.map((k, i) => `<span class="chip">` +
    `<button data-hd-move="${i}" data-by="-1" title="往前" ${i ? "" : "disabled"}>‹</button>${esc(hdColDef(k).label)}` +
    `<button data-hd-move="${i}" data-by="1" title="往后" ${i < last ? "" : "disabled"}>›</button></span>`).join("") +
    `<button class="secondary" data-hd-reset>恢复默认</button>`;
}

// Two stacked carets; the sort direction's one lit (CSS), both faint when the column isn't sorted
const hdSortIcon = dir => `<svg viewBox="0 0 10 14" width="10" height="14" aria-hidden="true">` +
  `<path class="${dir === "asc" ? "lit" : ""}" d="M5 1 9 6H1z"/><path class="${dir === "desc" ? "lit" : ""}" d="M5 13 1 8h8z"/></svg>`;
function renderHdRows() {
  const acc = hdAccount(), v = hd.valuation;
  $("hd-title").textContent = `${acc?.name ?? ""} 的持仓`;
  const curSel = (val, attr) => `<select ${attr}>${hd.currencies.map(c => `<option ${c === val ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  $("hd-currency").innerHTML = hdEdit
    ? `按 ${curSel(hdEdit.currency, 'data-hd-cur aria-label="账户币种"')} 计算市值`
    : `按 ${esc(v.currency ?? "—")} 计算市值`;
  const unlinked = (hdEdit ? hdEdit.rows : hd.rows).filter(r => r.kind === "security" && !r.symbol).length;
  if (unlinked) $("hd-currency").innerHTML += `。${unlinked} 个证券没有行情代码，价格要手填` +
    (hdEdit ? "，点代码旁的放大镜在 Yahoo Finance 查找" : "（点「修改」后可以在 Yahoo Finance 查找）");
  renderHdColBar();
  const cols = hdCols();
  // Sortable columns get 🔽 / 🔼 (read-only only): click for descending, again ascending, again as saved
  $("hd-head").innerHTML = `<tr>` + cols.map(c => {
    const label = esc(c.key === "converted" ? `折算（${v.currency ?? ""}）`
      : c.key === "gain" ? `持仓盈亏（${v.currency ?? "折算"}）` : c.label);
    if (!c.sort) return `<th>${label}</th>`;
    const on = !hdEdit && hdSort?.key === c.key;
    const btn = hdEdit ? "" : `<button class="sort-btn ${on ? "on" : ""}" data-hd-sort="${c.key}" ` +
      `title="${on ? (hdSort.dir === "desc" ? "现在是降序，点一下改升序" : "现在是升序，点一下取消排序") : "按这一列降序排列证券"}">` +
      `${hdSortIcon(on ? hdSort.dir : null)}</button>`;
    return `<th class="num">${label}${btn}</th>`;
  }).join("") + (hdEdit ? "<th></th>" : "") + `</tr>`;
  if (!hdEdit) {
    $("hd-rows").innerHTML = hdViewOrder().map(i => `<tr>` +
      cols.map(c => hdViewCell(c.key, hd.rows[i], v.rows[i])).join("") + `</tr>`).join("") ||
      `<tr><td colspan="${cols.length}" class="empty">这周还没填。${hd.copy_from
      ? `最近一次是 ${hdWeekLabel(hd.copy_from.week)} 那周，点「修改」会从那周的持仓开始填`
      : "点「修改」开始填"}</td></tr>`;
    $("hd-add").hidden = true;
    return;
  }
  $("hd-rows").innerHTML = hdEdit.rows.map((r, i) => `<tr>` + cols.map(c => hdEditCell(c.key, r, i, curSel)).join("") +
      `<td><button class="icon" data-hd-del="${i}" title="删掉这一行">✕</button></td></tr>`).join("") ||
    `<tr><td colspan="${cols.length + 1}" class="empty">没有持仓。用下面的按钮加一行；保存空的持仓会删掉这周的记录</td></tr>`;
  $("hd-add").hidden = false;
}
$("hd-head").addEventListener("click", e => {
  const key = e.target.closest("[data-hd-sort]")?.dataset.hdSort;
  if (!key) return;
  hdSort = hdSort?.key !== key ? { key, dir: "desc" } : hdSort.dir === "desc" ? { key, dir: "asc" } : null;
  save("tx.holdings.sort", JSON.stringify(hdSort));
  renderHdRows();
});
$("hd-cols-toggle").onclick = () => { hdColsOpen = !hdColsOpen; renderHdColBar(); };
$("hd-cols").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  if ("hdReset" in b.dataset) hdOrder = HD_COLS.map(c => c.key);
  else if (b.dataset.hdMove !== undefined) {
    const i = +b.dataset.hdMove, j = i + +b.dataset.by;
    [hdOrder[i], hdOrder[j]] = [hdOrder[j], hdOrder[i]];
  } else return;
  save("tx.holdings.cols", JSON.stringify(hdOrder));
  renderHdRows();
  $("hd-cols").querySelector(`[data-hd-move="${+b.dataset.hdMove + +(b.dataset.by ?? 0)}"][data-by="${b.dataset.by}"]`)?.focus();
});

function renderHdHistory() {
  const cur = esc(hd.valuation.currency ?? "");
  $("hd-history").innerHTML = hd.history.map(h => `<tr data-week="${h.week}" class="clickable ${h.week === hd.week ? "current" : ""}">
    <td>${hdWeekLabel(h.week)}</td>${moneyCell(h.market_value, "缺汇率")}${moneyCell(h.cost, "—")}` +
    `<td class="num ${h.gain == null ? "" : amtClass(h.gain)}">${h.gain == null ? "—" : hdSigned(h.gain)}</td>` +
    `<td class="num ${h.ratio == null ? "" : amtClass(h.ratio)}">${h.ratio == null ? "—" : fmtRatio(h.ratio)}</td>` +
    `<td class="num ${h.position_gain == null ? "" : amtClass(h.position_gain)}">${h.position_gain == null ? "—" : hdSigned(h.position_gain)}</td>` +
    `<td class="num ${h.position_ratio == null ? "" : amtClass(h.position_ratio)}">${h.position_ratio == null ? "—" : fmtRatio(h.position_ratio)}</td></tr>`).join("") ||
    `<tr><td colspan="7" class="empty">还没有记录</td></tr>`;
  $("hd-history-cur").textContent = cur ? `（${hd.valuation.currency}）` : "";
}

// ---------- editing ----------
const hdText = v => v == null ? "" : String(v);
const hdRowText = r => ({ kind: r.kind, name: r.name ?? "", code: r.code ?? "", currency: r.currency,
                          quantity: hdText(r.quantity), price: hdText(r.price), unit_cost: hdText(r.unit_cost),
                          amount: hdText(r.amount), symbol: r.symbol ?? "", price_source: r.price_source ?? "manual",
                          price_at: r.price_at ?? "", lookup: false });
// The week as typed, checked: [{...row with numbers}]; throws on a row that can't be saved.
// Not `final`: a new security still to be looked up may leave its price empty.
function hdClean(final = false) {
  return hdEdit.rows.map((r, i) => {
    const where = `第 ${i + 1} 行`;
    if (r.kind === "security") {
      if (!r.name.trim() && !r.code.trim()) throw new Error(`${where}：证券要填名称或代码`);
      const quantity = hdNum(r.quantity), price = hdNum(r.price);
      if (Number.isNaN(quantity)) throw new Error(`${where}：数量要是数字`);
      const later = !final && r.lookup && !r.symbol && r.price.trim() === "";
      if (!later && (Number.isNaN(price) || price < 0)) throw new Error(`${where}：现价要是不小于 0 的数字`);
      const unit_cost = r.unit_cost.trim() === "" ? null : hdNum(r.unit_cost);
      if (unit_cost !== null && !(unit_cost >= 0)) throw new Error(`${where}：成本价要是不小于 0 的数字（也可以不填）`);
      return { kind: r.kind, name: r.name.trim(), code: r.code.trim(), currency: r.currency, quantity,
               price: later ? null : price, unit_cost, symbol: r.symbol,
               price_source: r.symbol ? r.price_source : "manual", price_at: r.symbol && r.price_at || null };
    }
    const amount = hdNum(r.amount);
    if (Number.isNaN(amount)) throw new Error(`${where}：金额要是数字`);
    return { kind: r.kind, name: r.name.trim(), code: r.code.trim(), currency: r.currency, amount };
  });
}
const hdSame = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hdSaved = () => hd.rows.map(r => r.kind === "security"
  ? { kind: r.kind, name: r.name, code: r.code, currency: r.currency, quantity: r.quantity, price: r.price, unit_cost: r.unit_cost,
      symbol: r.symbol, price_source: r.price_source, price_at: r.price_at }
  : { kind: r.kind, name: r.name, code: r.code, currency: r.currency, amount: r.amount });
const hdRowLine = r => r.kind === "security"
  ? `证券 ${[r.name, r.code].filter(Boolean).join(" ")}：${fmtQty(r.quantity)} × ${r.price == null ? "（查行情）" : fmtQty(r.price)} ${r.currency}` +
    (r.symbol ? `（${r.symbol}，${r.price_source === "manual" ? "手填" : "自动"}）` : "") +
    (r.unit_cost == null ? "，没填成本价" : `，成本价 ${fmtQty(r.unit_cost)}`)
  : `现金${r.name ? " " + r.name : ""}：${fmtAmount(r.amount)} ${r.currency}`;
// The edits as lines for the confirm; throws on something that can't be saved
function hdChanges(final = false) {
  const rows = hdClean(final), out = [];
  if (hdEdit.currency !== hd.valuation.currency) out.push(`市值改按${hdEdit.currency}计算（原来是${hd.valuation.currency ?? "—"}）`);
  if (!hdSame(rows, hdSaved())) {
    if (!rows.length) out.push(`删掉 ${hdWeekLabel(hd.week)} 这周的持仓`);
    else out.push(`${hdWeekLabel(hd.week)} 这周的持仓（${rows.length} 行）：\n` + rows.map(r => "    " + hdRowLine(r)).join("\n"));
  }
  return out;
}
const hdPending = () => { if (!hdEdit) return 0; try { return hdChanges().length; } catch { return 1; } };

function renderHdLock() {
  const bar = $("hd-lock");
  const msg = hdLockMsg ? `<span class="lock-msg">${esc(hdLockMsg)}</span>` : "";
  if (!hdEdit) {
    bar.className = "lockbar";
    bar.innerHTML = `<span class="lock-state">🔒 只读</span>${msg || `<span class="muted">要填这周的持仓先点「修改」，改完统一保存</span>`}` +
      `<button data-lock="edit">✎ 修改</button>`;
    return;
  }
  let n = 0, problem = "";
  try { n = hdChanges().length; } catch (e) { problem = e.message; }
  const copied = hdEdit.copied ? `从 ${hdWeekLabel(hdEdit.copied)} 那周复制的，记得改成这周的现价` : "";
  bar.className = "lockbar editing";
  bar.innerHTML = `<span class="lock-state">✎ 正在修改</span>` +
    (problem ? `<span class="lock-msg warn">${esc(problem)}</span>` : msg ||
      `<span class="muted">${esc(copied || (n ? `${n} 处改动，还没保存` : "还没有改动"))}</span>`) +
    `<button class="secondary" data-lock="cancel">${n || problem ? "放弃" : "完成"}</button>` +
    `<button data-lock="save" ${n && !problem ? "" : "disabled"}>保存</button>`;
}
// Ends editing; with unsaved edits, asks first. False: the user wants to keep editing.
function leaveHdEdit() {
  if (!hdEdit) return true;
  const n = hdPending();
  if (n && !confirm("持仓有改动还没保存。\n点「确定」放弃这些改动，点「取消」回去接着改。")) return false;
  hdEdit = null;
  hdLockMsg = n ? "改动已放弃" : "";
  if (hd) renderHoldings();
  return true;
}
async function saveHdEdits() {
  // New securities: looked up first (「是这个」 / 仍然添加 / 取消 = not saved)
  for (let i = 0; i < hdEdit.rows.length; i++) {
    const r = hdEdit.rows[i];
    if (r.kind !== "security" || !r.lookup || r.symbol) continue;
    if (!(await hdFind(i, true))) { hdLockMsg = "没有保存"; return renderHdLock(); }
  }
  let list;
  try { list = hdChanges(true); } catch (e) { hdLockMsg = e.message; return renderHdLock(); }
  if (!list.length) return;
  if (!confirm(`保存？\n\n${list.map(t => "· " + t).join("\n")}`)) return;
  $("hd-lock").querySelector('[data-lock="save"]').disabled = true;
  const body = { account_id: hd.account, week: hd.week, rows: hdClean(true) };
  if (hdEdit.currency !== hd.valuation.currency) body.currency = hdEdit.currency;
  try {
    await api("/api/holdings", send("PUT", body));
  } catch (e) {
    hdLockMsg = "没有保存（一处也没改）：" + e.message;
    return renderHdLock();
  }
  hdEdit = null;
  hdLockMsg = "已保存";
  hdSynced.delete(hd.account); // prices for what was copied or linked
  await loadHoldings();
}
$("hd-lock").addEventListener("click", e => {
  const act = e.target.dataset.lock;
  if (!act) return;
  hdLockMsg = "";
  if (act === "edit") {
    const from = hd.filled ? hd.rows : hd.copy_from?.rows ?? [];
    const copied = !hd.filled && hd.copy_from ? hd.copy_from.week : null;
    hdEdit = { currency: hd.valuation.currency ?? hd.currencies[0], copied, rows: from.map(hdRowText).map(r =>
      // another week's price: fetched again for this one after saving
      copied && r.symbol && r.price_source !== "manual" ? { ...r, price_source: "live", price_at: "" } : r) };
    renderHoldings();
  }
  if (act === "cancel") leaveHdEdit();
  if (act === "save") saveHdEdits();
});
$("hd-add").addEventListener("click", e => {
  const kind = e.target.dataset.hdAdd;
  if (!kind || !hdEdit) return;
  const last = hdEdit.rows.at(-1);
  hdEdit.rows.push({ ...hdRowText({ kind, currency: last?.currency ?? hdEdit.currency }), lookup: kind === "security" });
  hdLockMsg = "";
  renderHdRows();
  renderHdLock();
  $("hd-rows").querySelector(`tr:last-child input`)?.focus();
});
$("hd-rows").addEventListener("click", async e => {
  const b = e.target.closest("button");
  if (!b || !hdEdit) return;
  const { hdDel, hdFind: find, hdUnlink, hdAuto } = b.dataset;
  if (hdDel !== undefined) hdEdit.rows.splice(+hdDel, 1);
  else if (find !== undefined) {
    const r = hdEdit.rows[+find];
    if (!(r.code || r.name).trim()) { hdLockMsg = "先填代码或名称再查找"; return renderHdLock(); }
    if (!(await hdFind(+find))) return;
  } else if (hdUnlink !== undefined) Object.assign(hdEdit.rows[+hdUnlink], { symbol: "", price_source: "manual", price_at: "" });
  else if (hdAuto !== undefined) {
    const r = hdEdit.rows[+hdAuto];
    b.disabled = true;
    try {
      const c = (await hdLookup(r.symbol)).find(c => c.symbol === r.symbol);
      if (!c) throw new Error(`Yahoo Finance 没有 ${r.symbol} 这周的价格`);
      Object.assign(r, { price: String(c.price), price_source: c.source, price_at: c.at });
      hdLockMsg = "";
    } catch (err) { hdLockMsg = err.message; }
  } else return;
  hdEdit.copied = null;
  renderHdRows();
  renderHdLock();
});

// ---------- looking a code up (Yahoo Finance) ----------
const hdLookup = code => api("/api/holdings/lookup?" + new URLSearchParams({ code, week: hd.week })).then(r => r.candidates);
// The dialog for row i: resolves "picked", "manual" (none of these: price typed in) or null (cancelled)
function hdFind(i, saving = false) {
  const r = hdEdit.rows[i], q = (r.code || r.name).trim(), dlg = $("hd-find");
  $("hd-find-title").textContent = saving ? `新证券「${q}」：是下面哪一个？` : `查找「${q}」的行情`;
  $("hd-find-msg").textContent = "正在查 Yahoo Finance…";
  $("hd-find-rows").innerHTML = "";
  $("hd-find-manual").textContent = saving ? "都不是，仍然添加（手填价格）" : "都不是，手填价格";
  dlg.showModal();
  return new Promise(resolve => {
    let found = [];
    const done = v => { dlg.onclick = dlg.oncancel = null; if (dlg.open) dlg.close(); resolve(v); };
    dlg.oncancel = e => { e.preventDefault(); done(null); };
    dlg.onclick = e => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.id === "hd-find-cancel") return done(null);
      if (b.id === "hd-find-manual") {
        Object.assign(r, { lookup: false, symbol: "", price_source: "manual", price_at: "" });
        return done("manual");
      }
      const c = found[+b.dataset.pick];
      if (!c) return;
      Object.assign(r, { symbol: c.symbol, price: String(c.price), price_source: c.source, price_at: c.at ?? "", lookup: false });
      if (!r.name.trim()) r.name = c.name;
      if (!r.code.trim()) r.code = c.symbol;
      done("picked");
      if (c.currency_name && c.currency_name !== r.currency &&
          confirm(`${c.symbol} 的价格是${c.currency_name}（${c.currency}），这一行填的是${r.currency}。\n要把这一行的币种改成${c.currency_name}吗？`))
        r.currency = c.currency_name;
    };
    hdLookup(q).then(list => {
      if (!dlg.open) return;
      found = list;
      $("hd-find-msg").textContent = list.length
        ? (hd.week === hd.this_week ? "价格是最新价。" : `价格是 ${hdWeekLabel(hd.week)} 那周最后一个交易日的收盘价。`) + "选对的那一个；名称保留你填的。"
        : "Yahoo Finance 没找到。可以换个写法再查：东京 7203.T、港股 0700.HK、上海 600519.SS、深圳 000858.SZ；名称要用英文（Tencent），中文名查不到。也可以手填价格。";
      $("hd-find-rows").innerHTML = list.map((c, k) => {
        const cur = c.currency_name ?? `${c.currency ?? "?"}（清单里没有）`;
        const warn = c.currency_name && c.currency_name !== r.currency ? `<span class="price-note warn">这一行填的是${esc(r.currency)}</span>` : "";
        return `<tr><td>${esc(c.symbol)}</td><td>${esc(c.name)}</td><td>${esc(c.exchange)}</td><td>${esc(cur)}${warn}</td>` +
          `<td class="num">${fmtQty(c.price)}<span class="price-note">${c.source === "close" ? `${+c.at.slice(5, 7)}/${+c.at.slice(8, 10)} 收盘` : "最新"}</span></td>` +
          `<td><button data-pick="${k}">是这个</button></td></tr>`;
      }).join("");
    }).catch(e => { if (dlg.open) $("hd-find-msg").textContent = "查找失败：" + e.message + "。可以稍后再试，或者手填价格。"; });
  });
}
// Typing: keep the text, mark bad numbers and update the row's amount without redrawing the inputs
$("hd-rows").addEventListener("input", e => {
  const { hd: i, k } = e.target.dataset;
  if (i === undefined || !hdEdit) return;
  const r = hdEdit.rows[+i];
  r[k] = e.target.value;
  if (k === "price" && r.symbol && r.price_source !== "manual") { // typed over: kept as typed from now on
    Object.assign(r, { price_source: "manual", price_at: "" });
    const note = e.target.closest("td").querySelector(".price-note");
    if (note) note.innerHTML = hdEditPriceNote(r, +i);
  }
  if (["quantity", "price", "amount"].includes(k)) e.target.classList.toggle("invalid", Number.isNaN(hdNum(r[k])));
  if (k === "unit_cost") e.target.classList.toggle("invalid", r[k].trim() !== "" && !(hdNum(r[k]) >= 0));
  if (r.kind === "security") {
    const own = hdNum(r.quantity) * hdNum(r.price);
    const cell = e.target.closest("tr").querySelector("[data-hd-own]");
    if (cell) cell.textContent = Number.isNaN(own) ? "" : fmtAmount(own);
  }
  hdEdit.copied = null;
  hdLockMsg = "";
  renderHdLock();
});
$("hd-rows").addEventListener("change", e => {
  if (e.target.dataset.k !== "currency" || !hdEdit) return;
  hdEdit.rows[+e.target.dataset.hd].currency = e.target.value;
  renderHdLock();
});
$("hd-currency").addEventListener("change", e => {
  if (!("hdCur" in e.target.dataset) || !hdEdit) return;
  hdEdit.currency = e.target.value;
  renderHdTiles();
  renderHdLock();
});
tableKeys($("hd-rows"), el => el.dataset.hd !== undefined && el.tagName === "INPUT" ? `input[data-k="${el.dataset.k}"]` : null);
window.addEventListener("beforeunload", e => { if (hdPending()) e.preventDefault(); });
