// 「持仓」: what each investment account holds on a day (the latest snapshot on or before it; typed
// in only when it changes), its market value against the balance sheet's balance (the cost), and
// every day's values since the first snapshot (chart and table). Read-only until 「✎ 修改」;
// 「保存」 saves the whole table from the chosen day on. Securities with a Yahoo Finance symbol are
// priced from there day by day (see portfolio.py): opening an account syncs it once per visit,
// 「更新价格」 fetches again, 🔍 looks a code up.

let hd = null;      // the server's page (GET /api/holdings)
let hdEdit = null;  // while editing: { currency, rows: [{ kind, name, code, currency, quantity, price, unit_cost, amount,
                    //   symbol, price_source, price_at, lookup (new: looked up before saving) }] as typed }
let hdSynced = new Set(); // accounts whose prices were synced this visit (main.js clears it)
let hdPriceMsg = "";      // what the last price fetch said
let hdLockMsg = "";
let hdPreset = null; // { account, day }: set by the balance sheet's links, used by the next visit
let hdAllDays = false; // the daily table: every day, not only the latest ones
const HD_KINDS = { security: "证券", cash: "现金" };

// Days are "2026-11-14"
const hdDate = d => new Date(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10));
const hdShift = (d, days) => { const x = hdDate(d); x.setDate(x.getDate() + days); return ymd(x); };
const HD_WEEKDAYS = "日一二三四五六";
const hdLabel = d => `${d.replace(/-/g, "/")}（周${HD_WEEKDAYS[hdDate(d).getDay()]}）`;
const hdShort = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;
const fmtQty = n => n.toLocaleString("en-US", { maximumFractionDigits: 6 });
const fmtRatio = r => (r > 0 ? "+" : "") + (r * 100).toFixed(2) + "%";
const hdSigned = n => (n > 0 ? "+" : "") + fmtAmount(n);
// "" and anything not a number: NaN
const hdNum = t => { const s = String(t ?? "").replace(/[,\s]/g, ""); return s === "" ? NaN : Number(s); };

async function loadHoldings(accountId = hd?.account ?? (+load("tx.holdings.account") || null), day = hd?.day) {
  const q = new URLSearchParams();
  if (accountId != null) q.set("account_id", accountId);
  if (day) q.set("day", day);
  try {
    hd = await api("/api/holdings?" + q);
  } catch (e) {
    if (accountId != null) return loadHoldings(null, day); // that account is gone or no longer 投资
    return flash("hd-status", "读取失败：" + e.message);
  }
  flash("hd-status", hdPriceMsg);
  if (hd.account != null) save("tx.holdings.account", String(hd.account));
  renderHoldings();
  if (hd.account != null && !hdEdit && !hdSynced.has(hd.account)) hdSync(hd.account);
}
// The daily prices and rates the account needs, unless fetched lately (server side)
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
// Switching account or day while editing: asked first
function hdGo(accountId, day) {
  if (!leaveHdEdit()) return renderHoldings(); // puts the selects back
  loadHoldings(accountId, day > hd.today ? hd.today : day);
}
$("hd-account").addEventListener("change", e => hdGo(+e.target.value, hd.day));
$("hd-prev").onclick = () => hdGo(hd.account, hdShift(hd.day, -1));
$("hd-next").onclick = () => hdGo(hd.account, hdShift(hd.day, 1));
$("hd-today").onclick = () => hdGo(hd.account, hd.today);
$("hd-day").addEventListener("change", e => { if (e.target.value) hdGo(hd.account, e.target.value); });
$("hd-history").addEventListener("click", e => { const tr = e.target.closest("tr[data-day]"); if (tr) hdGo(hd.account, tr.dataset.day); });
$("hd-history-more").onclick = () => { hdAllDays = !hdAllDays; renderHdHistory(); };

const hdAccount = () => hd.accounts.find(a => a.id === hd.account);
function renderHoldings() {
  const none = !hd.accounts.length;
  $("hd-warn").innerHTML = none
    ? `<div class="warn-box">还没有投资账户。去「<a href="#settings">设置 › 账户</a>」点「修改」，把账户的类型设为「投资」。</div>` : "";
  for (const id of ["hd-tiles", "hd-card", "hd-history-card"]) $(id).hidden = none;
  $("hd-account").innerHTML = hd.accounts.map(a => `<option value="${a.id}" ${a.id === hd.account ? "selected" : ""}>${esc(a.name)}</option>`).join("");
  $("hd-day").value = hd.day;
  $("hd-day").max = hd.today;
  $("hd-next").disabled = hd.day >= hd.today;
  $("hd-today").disabled = hd.day === hd.today;
  $("hd-refresh").hidden = none || !hd.changes.length;
  $("hd-refresh").disabled = !!hdEdit;
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
    <div class="label">市值 · ${hdLabel(hd.day)}${!hd.snapshot ? "（这天之前还没填过持仓）"
      : hd.snapshot === hd.day ? "（这天有变动）" : `（用 ${hdShort(hd.snapshot)} 填的持仓）`}</div>
    <div class="row"><span class="total">${money(v.market_value)}</span></div>
    <div class="split gains">${part("较结余", v.cost, v.gain, v.ratio, `<span class="muted">成本 = 结余页这天结束时的余额，市值含现金</span>`)}
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
    // With a symbol the price comes from Yahoo Finance; without one it is typed in
    case "price": return !sec ? none : r.symbol
      ? `<td class="num">${r.price === "" ? "—" : fmtQty(hdNum(r.price))}<span class="price-note">自动 · ${hdPriceNote(r)}</span></td>`
      : `<td class="num">${inp("price", r.lookup && r.price.trim() === "" ? "" : bad("price"))}</td>`;
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
  if (r.price_source === "saved" || !r.price_at) return "还没取到，用填的价格";
  if (r.price_source === "close") return `${+r.price_at.slice(5, 7)}/${+r.price_at.slice(8, 10)} 收盘`;
  const d = new Date(r.price_at);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())} 最新`;
};
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
      `<tr><td colspan="${cols.length}" class="empty">这天之前还没填过持仓。点「修改」从这天开始填</td></tr>`;
    $("hd-add").hidden = true;
    return;
  }
  $("hd-rows").innerHTML = hdEdit.rows.map((r, i) => `<tr>` + cols.map(c => hdEditCell(c.key, r, i, curSel)).join("") +
      `<td><button class="icon" data-hd-del="${i}" title="删掉这一行">✕</button></td></tr>`).join("") ||
    `<tr><td colspan="${cols.length + 1}" class="empty">没有持仓。用下面的按钮加一行；全卖掉了可以留一行金额为 0 的现金</td></tr>`;
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

// ---------- every day ----------
const HD_SHOWN_DAYS = 31;
function renderHdHistory() {
  const cur = hd.valuation.currency ?? "";
  $("hd-history-cur").textContent = cur ? `（${cur}）` : "";
  renderHdChart();
  const prev = i => i ? hd.series[i - 1].market_value : null;
  const days = hd.series.map((s, i) => ({ ...s, delta: s.market_value != null && prev(i) != null
    ? Math.round((s.market_value - prev(i)) * 100) / 100 : null })).reverse();
  const shown = hdAllDays ? days : days.slice(0, HD_SHOWN_DAYS);
  const signed = x => `<td class="num ${x == null ? "" : amtClass(x)}">${x == null ? "—" : hdSigned(x)}</td>`;
  const ratio = x => `<td class="num ${x == null ? "" : amtClass(x)}">${x == null ? "—" : fmtRatio(x)}</td>`;
  $("hd-history").innerHTML = shown.map(h => `<tr data-day="${h.day}" class="clickable ${h.day === hd.day ? "current" : ""}">
    <td>${hdLabel(h.day)}${h.change ? `<span class="count">变动</span>` : ""}</td>${moneyCell(h.market_value, "缺汇率")}${signed(h.delta)}` +
    `${moneyCell(h.cost, "—")}${signed(h.gain)}${ratio(h.ratio)}${signed(h.position_gain)}${ratio(h.position_ratio)}</tr>`).join("") ||
    `<tr><td colspan="8" class="empty">还没有记录</td></tr>`;
  const more = $("hd-history-more");
  more.hidden = days.length <= HD_SHOWN_DAYS;
  more.textContent = hdAllDays ? `只看最近 ${HD_SHOWN_DAYS} 天` : `显示全部 ${days.length} 天`;
}

// Market value and cost (the balance) by day: one axis (both in the account's currency), a legend,
// a crosshair with both values, ticks under the days with a change; a click opens that day.
const HD_SERIES = [{ key: "market_value", label: "市值", color: "#2a78d6" }, { key: "cost", label: "成本（结余）", color: "#eb6834" }];
function renderHdChart() {
  const box = $("hd-chart"), pts = hd.series;
  const vals = pts.flatMap(p => HD_SERIES.map(s => p[s.key])).filter(v => v != null);
  if (pts.length < 2 || !vals.length) { box.innerHTML = ""; return; }
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= Math.abs(lo) * 0.05 || 1; hi += Math.abs(hi) * 0.05 || 1; }
  const margin = (hi - lo) * 0.08;
  lo -= margin; hi += margin;
  // Drawn at the box's own width (not scaled), the left margin as wide as the longest axis label
  const ticks = [0, 1, 2, 3].map(k => lo + (hi - lo) * (k + 0.5) / 4);
  const tickText = v => Math.round(v).toLocaleString("en-US");
  const W = Math.max(300, box.clientWidth || 720), H = 180, R = 12, T = 12, B = 26, n = pts.length - 1;
  const L = 12 + 7 * Math.max(...ticks.map(v => tickText(v).length));
  const x = i => L + (W - L - R) * i / n, y = v => T + (H - T - B) * (hi - v) / (hi - lo);
  // A line breaks where a value is missing
  const path = key => pts.map((p, i) => p[key] == null ? "" :
    `${i && pts[i - 1][key] != null ? "L" : "M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  const xTicks = [...new Set([0, Math.round(n / 2), n])];
  box.innerHTML = `<div class="hd-legend">${HD_SERIES.map(s => `<span><i style="background:${s.color}"></i>${s.label}</span>`).join("")}` +
    `<span><i class="tick"></i>持仓变动</span></div>` +
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="每天的市值和成本">` +
    ticks.map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>` +
      `<text class="axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${tickText(v)}</text>`).join("") +
    xTicks.map(i => `<text class="axis" x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === n ? "end" : "middle"}">${hdShort(pts[i].day)}</text>`).join("") +
    pts.map((p, i) => p.change ? `<line class="change" x1="${x(i)}" x2="${x(i)}" y1="${H - B}" y2="${H - B + 5}"/>` : "").join("") +
    HD_SERIES.map(s => `<path d="${path(s.key)}" stroke="${s.color}" fill="none" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`).join("") +
    `<g class="cross" visibility="hidden"><line class="cross-line" y1="${T}" y2="${H - B}"/>` +
    HD_SERIES.map(s => `<circle r="4" fill="${s.color}" stroke="#fff" stroke-width="2" data-k="${s.key}"/>`).join("") + `</g>` +
    `<rect x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent"/></svg>` +
    `<div class="hd-tip" hidden></div>`;
  const svg = box.querySelector("svg"), cross = svg.querySelector(".cross"), tip = box.querySelector(".hd-tip");
  const at = e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
    return Math.max(0, Math.min(n, Math.round((px - L) / (W - L - R) * n)));
  };
  svg.onpointermove = e => {
    const i = at(e), p = pts[i], cx = x(i), line = cross.querySelector("line");
    cross.setAttribute("visibility", "visible");
    line.setAttribute("x1", cx);
    line.setAttribute("x2", cx);
    for (const c of cross.querySelectorAll("circle")) {
      const v = p[c.dataset.k];
      c.setAttribute("visibility", v == null ? "hidden" : "inherit");
      if (v != null) { c.setAttribute("cx", cx); c.setAttribute("cy", y(v)); }
    }
    tip.hidden = false;
    tip.innerHTML = `<b>${hdLabel(p.day)}${p.change ? " · 变动" : ""}</b>` + HD_SERIES.map(s =>
      `<div><i style="background:${s.color}"></i>${s.label} ${p[s.key] == null ? "—" : fmtAmount(p[s.key])}</div>`).join("") +
      (p.gain == null ? "" : `<div>较结余 <span class="${amtClass(p.gain)}">${hdSigned(p.gain)}</span></div>`);
    const r = svg.getBoundingClientRect(), left = cx * r.width / W;
    tip.style.left = `${Math.max(0, Math.min(left + 12, r.width - tip.offsetWidth - 4))}px`;
  };
  svg.onpointerleave = () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; };
  svg.onclick = e => hdGo(hd.account, pts[at(e)].day);
}
// Drawn at the page's width: again when the window is resized
let hdChartTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(hdChartTimer);
  hdChartTimer = setTimeout(() => { if (hd && !$("view-holdings").hidden) renderHdChart(); }, 150);
});

// ---------- editing ----------
const hdText = v => v == null ? "" : String(v);
const hdRowText = r => ({ kind: r.kind, name: r.name ?? "", code: r.code ?? "", currency: r.currency,
                          quantity: hdText(r.quantity), price: hdText(r.price), unit_cost: hdText(r.unit_cost),
                          amount: hdText(r.amount), symbol: r.symbol ?? "", price_source: r.price_source ?? "manual",
                          price_at: r.price_at ?? "", lookup: false });
// The table as typed, checked: [{...row with numbers}]; throws on a row that can't be saved.
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
               price: later ? null : price, unit_cost, symbol: r.symbol };
    }
    const amount = hdNum(r.amount);
    if (Number.isNaN(amount)) throw new Error(`${where}：金额要是数字`);
    return { kind: r.kind, name: r.name.trim(), code: r.code.trim(), currency: r.currency, amount };
  });
}
const hdSame = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hdSaved = () => hd.rows.map(r => r.kind === "security"
  ? { kind: r.kind, name: r.name, code: r.code, currency: r.currency, quantity: r.quantity, price: r.price, unit_cost: r.unit_cost,
      symbol: r.symbol }
  : { kind: r.kind, name: r.name, code: r.code, currency: r.currency, amount: r.amount });
const hdRowLine = r => r.kind === "security"
  ? `证券 ${[r.name, r.code].filter(Boolean).join(" ")}：${fmtQty(r.quantity)} × ${r.price == null ? "（查行情）" : fmtQty(r.price)} ${r.currency}` +
    (r.symbol ? `（${r.symbol}，自动取价）` : "（手填价格）") +
    (r.unit_cost == null ? "，没填成本价" : `，成本价 ${fmtQty(r.unit_cost)}`)
  : `现金${r.name ? " " + r.name : ""}：${fmtAmount(r.amount)} ${r.currency}`;
// The edits as lines for the confirm; throws on something that can't be saved
function hdChanges(final = false) {
  const rows = hdClean(final), out = [];
  if (hdEdit.currency !== hd.valuation.currency) out.push(`市值改按${hdEdit.currency}计算（原来是${hd.valuation.currency ?? "—"}）`);
  if (!hdSame(rows, hdSaved())) {
    if (!rows.length) throw new Error("至少要有一行；全卖掉了可以留一行金额为 0 的现金");
    out.push(`${hdLabel(hd.day)} 起的持仓（${rows.length} 行）` +
      (hd.snapshot && hd.snapshot !== hd.day ? `，${hdShort(hd.snapshot)} 填的那份用到前一天为止` : "") +
      "：\n" + rows.map(r => "    " + hdRowLine(r)).join("\n"));
  }
  return out;
}
const hdPending = () => { if (!hdEdit) return 0; try { return hdChanges().length; } catch { return 1; } };

function renderHdLock() {
  const bar = $("hd-lock");
  const msg = hdLockMsg ? `<span class="lock-msg">${esc(hdLockMsg)}</span>` : "";
  if (!hdEdit) {
    bar.className = "lockbar";
    bar.innerHTML = `<span class="lock-state">🔒 只读</span>${msg || `<span class="muted">持仓有变动时，选好那天点「修改」，改完统一保存</span>`}` +
      (hd.snapshot && hd.snapshot === hd.day ? `<button class="secondary" data-lock="delete" title="之前的持仓会一直用到下一次变动">删除这天的变动</button>` : "") +
      `<button data-lock="edit">✎ 修改</button>`;
    return;
  }
  let n = 0, problem = "";
  try { n = hdChanges().length; } catch (e) { problem = e.message; }
  const copied = hdEdit.copied ? `从 ${hdShort(hdEdit.copied)} 填的持仓开始改，保存后从 ${hdShort(hd.day)} 起是新的一份` : "";
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
  const rows = hdClean(true), body = { account_id: hd.account, day: hd.day };
  if (!hdSame(rows, hdSaved())) body.rows = rows; // left out: only the currency changes
  if (hdEdit.currency !== hd.valuation.currency) body.currency = hdEdit.currency;
  try {
    await api("/api/holdings", send("PUT", body));
  } catch (e) {
    hdLockMsg = "没有保存（一处也没改）：" + e.message;
    return renderHdLock();
  }
  hdEdit = null;
  hdLockMsg = "已保存";
  hdSynced.delete(hd.account); // prices for what was added or linked
  await loadHoldings();
}
$("hd-lock").addEventListener("click", e => {
  const act = e.target.dataset.lock;
  if (!act) return;
  hdLockMsg = "";
  if (act === "edit") {
    const copied = hd.snapshot && hd.snapshot !== hd.day ? hd.snapshot : null;
    hdEdit = { currency: hd.valuation.currency ?? hd.currencies[0], copied, rows: hd.rows.map(hdRowText) };
    renderHoldings();
  }
  if (act === "delete") deleteHdDay();
  if (act === "cancel") leaveHdEdit();
  if (act === "save") saveHdEdits();
});
async function deleteHdDay() {
  const before = hd.changes.find(d => d < hd.day);
  if (!confirm(`删除 ${hdLabel(hd.day)} 这天的持仓变动？\n` +
    (before ? `之后会一直用 ${hdShort(before)} 填的持仓，直到下一次变动。` : "这是最早的一份，删除后这天之前就没有持仓了。"))) return;
  try {
    await api("/api/holdings?" + new URLSearchParams({ account_id: hd.account, day: hd.day }), { method: "DELETE" });
    hdLockMsg = "已删除";
  } catch (e) { hdLockMsg = "没有删除：" + e.message; }
  await loadHoldings();
}
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
  const { hdDel, hdFind: find, hdUnlink } = b.dataset;
  if (hdDel !== undefined) hdEdit.rows.splice(+hdDel, 1);
  else if (find !== undefined) {
    const r = hdEdit.rows[+find];
    if (!(r.code || r.name).trim()) { hdLockMsg = "先填代码或名称再查找"; return renderHdLock(); }
    if (!(await hdFind(+find))) return;
  } else if (hdUnlink !== undefined) Object.assign(hdEdit.rows[+hdUnlink], { symbol: "", price_source: "manual", price_at: "" });
  else return;
  hdEdit.copied = null;
  renderHdRows();
  renderHdLock();
});

// ---------- looking a code up (Yahoo Finance) ----------
const hdLookup = code => api("/api/holdings/lookup?" + new URLSearchParams({ code, day: hd.day })).then(r => r.candidates);
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
        ? (hd.day === hd.today ? "价格是最新价。" : `价格是 ${hdShort(hd.day)}（或之前最后一个交易日）的收盘价。`) + "选对的那一个；名称保留你填的。"
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
