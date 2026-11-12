// 「持仓」: what each investment account holds, one snapshot a week, its market value against the
// balance sheet's balance (the cost). Read-only until 「✎ 修改」; 「保存」 saves the whole week at once.

let hd = null;      // the server's page (GET /api/holdings)
let hdEdit = null;  // while editing: { currency, rows: [{ kind, name, code, currency, quantity, price, amount }] as typed
let hdLockMsg = "";
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
  flash("hd-status", "");
  if (hd.account != null) save("tx.holdings.account", String(hd.account));
  renderHoldings();
}
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

function renderHdRows() {
  const acc = hdAccount(), v = hd.valuation;
  $("hd-title").textContent = `${acc?.name ?? ""} 的持仓`;
  const curSel = (val, attr) => `<select ${attr}>${hd.currencies.map(c => `<option ${c === val ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  $("hd-currency").innerHTML = hdEdit
    ? `按 ${curSel(hdEdit.currency, 'data-hd-cur aria-label="账户币种"')} 计算市值`
    : `按 ${esc(v.currency ?? "—")} 计算市值`;
  const head = `<tr><th>类型</th><th>名称</th><th>代码</th><th>币种</th><th class="num">数量</th><th class="num">现价</th>` +
    `<th class="num">成本价</th><th class="num">金额</th>` +
    (hdEdit ? "<th></th>" : `<th class="num">折算（${esc(v.currency ?? "")}）</th><th class="num">持仓盈亏</th>`) + `</tr>`;
  $("hd-head").innerHTML = head;
  if (!hdEdit) {
    $("hd-rows").innerHTML = hd.rows.map((r, i) => {
      const sec = r.kind === "security", val = v.rows[i];
      const gain = !sec || val.cost == null ? `<td class="none">—</td>`
        : val.gain == null ? `<td class="missing">缺汇率</td>`
        : `<td class="num ${amtClass(val.gain)}">${hdSigned(val.gain)}${val.ratio == null ? "" : `<span class="pct">${fmtRatio(val.ratio)}</span>`}</td>`;
      return `<tr><td>${HD_KINDS[r.kind]}</td><td>${esc(r.name)}</td><td>${esc(r.code)}</td><td>${esc(r.currency)}</td>` +
        (sec ? `<td class="num">${fmtQty(r.quantity)}</td><td class="num">${fmtQty(r.price)}</td>` +
               (r.unit_cost == null ? `<td class="none">—</td>` : `<td class="num">${fmtQty(r.unit_cost)}</td>`)
             : `<td class="none">—</td><td class="none">—</td><td class="none">—</td>`) +
        moneyCell(val.value) + moneyCell(val.converted) + gain + `</tr>`;
    }).join("") || `<tr><td colspan="10" class="empty">这周还没填。${hd.copy_from
      ? `最近一次是 ${hdWeekLabel(hd.copy_from.week)} 那周，点「修改」会从那周的持仓开始填`
      : "点「修改」开始填"}</td></tr>`;
    $("hd-add").hidden = true;
    return;
  }
  $("hd-rows").innerHTML = hdEdit.rows.map((r, i) => {
    const sec = r.kind === "security", inp = (k, cls = "") =>
      `<input data-hd="${i}" data-k="${k}" value="${esc(r[k])}" class="${cls}"${["quantity", "price", "unit_cost", "amount"].includes(k) ? ' inputmode="decimal"' : ""}${k === "unit_cost" ? ' placeholder="可不填"' : ""}>`;
    const bad = k => Number.isNaN(hdNum(r[k])) ? "invalid" : "";
    const badCost = String(r.unit_cost).trim() !== "" && !(hdNum(r.unit_cost) >= 0) ? "invalid" : "";
    const own = sec ? hdNum(r.quantity) * hdNum(r.price) : hdNum(r.amount);
    return `<tr><td>${HD_KINDS[r.kind]}</td><td>${inp("name")}</td><td>${inp("code")}</td>` +
      `<td>${curSel(r.currency, `data-hd="${i}" data-k="currency"`)}</td>` +
      (sec ? `<td class="num">${inp("quantity", bad("quantity"))}</td><td class="num">${inp("price", bad("price"))}</td>` +
             `<td class="num">${inp("unit_cost", badCost)}</td><td class="num">${Number.isNaN(own) ? "" : fmtAmount(own)}</td>`
           : `<td class="none">—</td><td class="none">—</td><td class="none">—</td><td class="num">${inp("amount", bad("amount"))}</td>`) +
      `<td><button class="icon" data-hd-del="${i}" title="删掉这一行">✕</button></td></tr>`;
  }).join("") || `<tr><td colspan="9" class="empty">没有持仓。用下面的按钮加一行；保存空的持仓会删掉这周的记录</td></tr>`;
  $("hd-add").hidden = false;
}

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
                          amount: hdText(r.amount) });
// The week as typed, checked: [{...row with numbers}]; throws on a row that can't be saved
function hdClean() {
  return hdEdit.rows.map((r, i) => {
    const where = `第 ${i + 1} 行`;
    if (r.kind === "security") {
      if (!r.name.trim() && !r.code.trim()) throw new Error(`${where}：证券要填名称或代码`);
      const quantity = hdNum(r.quantity), price = hdNum(r.price);
      if (Number.isNaN(quantity)) throw new Error(`${where}：数量要是数字`);
      if (Number.isNaN(price) || price < 0) throw new Error(`${where}：现价要是不小于 0 的数字`);
      const unit_cost = r.unit_cost.trim() === "" ? null : hdNum(r.unit_cost);
      if (unit_cost !== null && !(unit_cost >= 0)) throw new Error(`${where}：成本价要是不小于 0 的数字（也可以不填）`);
      return { kind: r.kind, name: r.name.trim(), code: r.code.trim(), currency: r.currency, quantity, price, unit_cost };
    }
    const amount = hdNum(r.amount);
    if (Number.isNaN(amount)) throw new Error(`${where}：金额要是数字`);
    return { kind: r.kind, name: r.name.trim(), code: r.code.trim(), currency: r.currency, amount };
  });
}
const hdSame = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hdSaved = () => hd.rows.map(r => r.kind === "security"
  ? { kind: r.kind, name: r.name, code: r.code, currency: r.currency, quantity: r.quantity, price: r.price, unit_cost: r.unit_cost }
  : { kind: r.kind, name: r.name, code: r.code, currency: r.currency, amount: r.amount });
const hdRowLine = r => r.kind === "security"
  ? `证券 ${[r.name, r.code].filter(Boolean).join(" ")}：${fmtQty(r.quantity)} × ${fmtQty(r.price)} ${r.currency}` +
    (r.unit_cost == null ? "，没填成本价" : `，成本价 ${fmtQty(r.unit_cost)}`)
  : `现金${r.name ? " " + r.name : ""}：${fmtAmount(r.amount)} ${r.currency}`;
// The edits as lines for the confirm; throws on something that can't be saved
function hdChanges() {
  const rows = hdClean(), out = [];
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
  let list;
  try { list = hdChanges(); } catch (e) { hdLockMsg = e.message; return renderHdLock(); }
  if (!list.length) return;
  if (!confirm(`保存？\n\n${list.map(t => "· " + t).join("\n")}`)) return;
  $("hd-lock").querySelector('[data-lock="save"]').disabled = true;
  const body = { account_id: hd.account, week: hd.week, rows: hdClean() };
  if (hdEdit.currency !== hd.valuation.currency) body.currency = hdEdit.currency;
  try {
    await api("/api/holdings", send("PUT", body));
  } catch (e) {
    hdLockMsg = "没有保存（一处也没改）：" + e.message;
    return renderHdLock();
  }
  hdEdit = null;
  hdLockMsg = "已保存";
  await loadHoldings();
}
$("hd-lock").addEventListener("click", e => {
  const act = e.target.dataset.lock;
  if (!act) return;
  hdLockMsg = "";
  if (act === "edit") {
    const from = hd.filled ? hd.rows : hd.copy_from?.rows ?? [];
    hdEdit = { currency: hd.valuation.currency ?? hd.currencies[0], rows: from.map(hdRowText),
               copied: !hd.filled && hd.copy_from ? hd.copy_from.week : null };
    renderHoldings();
  }
  if (act === "cancel") leaveHdEdit();
  if (act === "save") saveHdEdits();
});
$("hd-add").addEventListener("click", e => {
  const kind = e.target.dataset.hdAdd;
  if (!kind || !hdEdit) return;
  const last = hdEdit.rows.at(-1);
  hdEdit.rows.push(hdRowText({ kind, currency: last?.currency ?? hdEdit.currency }));
  hdLockMsg = "";
  renderHdRows();
  renderHdLock();
  $("hd-rows").querySelector(`tr:last-child input`)?.focus();
});
$("hd-rows").addEventListener("click", e => {
  const i = e.target.dataset.hdDel;
  if (i === undefined || !hdEdit) return;
  hdEdit.rows.splice(+i, 1);
  renderHdRows();
  renderHdLock();
});
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
    e.target.closest("tr").children[7].textContent = Number.isNaN(own) ? "" : fmtAmount(own);
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
