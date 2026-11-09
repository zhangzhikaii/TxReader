// 「交易」: kept transactions, filters, editing category and description.

$("tx-source").innerHTML = `<option value="">全部</option><option value="manual">手动输入</option><option value="email">邮件导入</option>` +
  `<optgroup label="截图识别">` +
  Object.entries(SOURCE_LABELS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("") + `</optgroup>`;
function filterQuery(p) {
  const q = new URLSearchParams();
  const fields = { source: "source", date_from: "from", date_to: "to", account: "account", currency: "currency", category: "category" };
  for (const [param, id] of Object.entries(fields)) {
    const el = $(`${p}-${id}`);
    if (el?.value) q.set(param, el.value);
  }
  return q.toString();
}
// Period bar: a day / week / month / year containing a date, stepped with ‹ ›.
// It only sets the from / to dates of 高级筛选; other dates there show as 自定义.
const PERIODS = ["day", "week", "month", "year"];
let txPeriod = PERIODS.includes(load("tx.period")) ? load("tx.period") : "month";
const parseYmd = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
function periodRange(kind, d) {
  const y = d.getFullYear(), m = d.getMonth(), day = d.getDate();
  if (kind === "day") return [d, d];
  if (kind === "week") { // Monday to Sunday
    const s = new Date(y, m, day - (d.getDay() + 6) % 7);
    return [s, new Date(s.getFullYear(), s.getMonth(), s.getDate() + 6)];
  }
  if (kind === "month") return [new Date(y, m, 1), new Date(y, m + 1, 0)];
  return [new Date(y, 0, 1), new Date(y, 11, 31)];
}
function shiftPeriod(kind, d, n) {
  const y = d.getFullYear(), m = d.getMonth(), day = d.getDate();
  if (kind === "day") return new Date(y, m, day + n);
  if (kind === "week") return new Date(y, m, day + 7 * n);
  if (kind === "month") return new Date(y, m + n, 1);
  return new Date(y + n, 0, 1);
}
function setPeriod(kind, anchor) {
  const [a, b] = periodRange(kind, anchor);
  txPeriod = kind;
  save("tx.period", kind);
  $("tx-from").value = ymd(a);
  $("tx-to").value = ymd(b);
}
// Which period the chosen dates are (the last used kind first), or custom
function currentPeriod() {
  const from = $("tx-from").value, to = $("tx-to").value;
  if (!from || !to) return null;
  for (const kind of [txPeriod, ...PERIODS.filter(k => k !== txPeriod)]) {
    const [a, b] = periodRange(kind, parseYmd(from));
    if (ymd(a) === from && ymd(b) === to) return kind;
  }
  return null;
}
const WEEKDAYS = "日一二三四五六";
const fmtDay = (d, withYear = true) => `${withYear ? d.getFullYear() + "年" : ""}${d.getMonth() + 1}月${d.getDate()}日`;
function showPeriod() {
  const kind = currentPeriod(), from = $("tx-from").value, to = $("tx-to").value;
  if (kind && kind !== txPeriod) { txPeriod = kind; save("tx.period", kind); }
  $("tx-period").value = kind ?? "custom";
  $("tx-prev").disabled = $("tx-next").disabled = !kind;
  let label;
  if (kind) {
    const a = parseYmd(from), b = parseYmd(to);
    label = kind === "day" ? `${fmtDay(a)}（周${WEEKDAYS[a.getDay()]}）`
      : kind === "week" ? `${fmtDay(a)} – ${fmtDay(b, b.getFullYear() !== a.getFullYear())}`
      : kind === "month" ? fmtMonth(from.slice(0, 7))
      : `${a.getFullYear()}年`;
  } else {
    label = from && to ? `${from} – ${to}` : from ? `${from} 起` : to ? `至 ${to}` : "全部日期";
  }
  $("tx-range").textContent = label;
  // 高级筛选 shows how many of its other filters are in use, so a closed panel is not forgotten
  const used = ["tx-account", "tx-currency", "tx-category", "tx-source"].filter(id => $(id).value).length;
  $("tx-adv-toggle").textContent = "高级筛选" + (used ? ` · ${used}` : "") + ($("tx-adv").hidden ? " ▾" : " ▴");
  $("tx-adv-toggle").classList.toggle("active", used > 0);
}
function showAdvanced(open) {
  $("tx-adv").hidden = !open;
  $("tx-adv-toggle").setAttribute("aria-expanded", open);
  save("tx.advOpen", open ? "1" : "0");
  showPeriod();
}
$("tx-adv-toggle").onclick = () => showAdvanced($("tx-adv").hidden);
$("tx-period").addEventListener("change", e => {
  const from = $("tx-from").value;
  setPeriod(e.target.value, from ? parseYmd(from) : new Date());
  loadTransactions();
});
for (const [id, n] of [["tx-prev", -1], ["tx-next", 1]]) $(id).onclick = () => {
  setPeriod(txPeriod, shiftPeriod(txPeriod, parseYmd($("tx-from").value), n));
  loadTransactions();
};
$("tx-today").onclick = () => { setPeriod(currentPeriod() ?? txPeriod, new Date()); loadTransactions(); };
// The page opens on the period containing today; after that the dates are what was chosen
setPeriod(txPeriod, new Date());
showAdvanced(load("tx.advOpen") === "1");
// Account / currency / category filters offer the settings lists, keeping what is chosen
function fillTxFilters() {
  const fill = (id, label, names) => {
    const el = $(id), keep = el.value;
    el.innerHTML = `<option value="">全部${label}</option>` + names.map(n => `<option>${esc(n)}</option>`).join("");
    el.value = names.includes(keep) ? keep : "";
  };
  fill("tx-account", "账户", sugg.accounts.map(a => a.name));
  fill("tx-currency", "币种", sugg.currencies);
  fill("tx-category", "种类", sugg.categories);
  // Opened from a cell of 收支 › 每月对比: that month, category and currency only
  if (txPreset) {
    const p = txPreset;
    txPreset = null;
    txPeriod = "month";
    $("tx-from").value = p.from;
    $("tx-to").value = p.to;
    $("tx-account").value = $("tx-source").value = "";
    for (const [id, v] of [["tx-category", p.category], ["tx-currency", p.currency]]) {
      if (v && ![...$(id).options].some(o => o.value === v)) $(id).insertAdjacentHTML("beforeend", `<option>${esc(v)}</option>`);
      $(id).value = v;
    }
    showAdvanced(true); // show which category / currency is filtered
  }
}
let txPreset = null;

let txRows = [];
async function loadTransactions() {
  showPeriod();
  const query = filterQuery("tx");
  try {
    txRows = (await api("/api/transactions?" + query)).transactions;
  } catch (e) {
    return flash("tx-status", "读取失败：" + e.message);
  }
  renderTxRows();
  flash("tx-status", txRows.length ? `共 ${txRows.length} 条` : "没有符合条件的交易");
  $("tx-export").disabled = !txRows.length;
  txQuery = query;
}
// Export what is listed: the same filters, asked of the server again
let txQuery = "";
$("tx-export").onclick = async () => {
  const now = new Date();
  const filename = `txreader_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.csv`;
  $("tx-export").disabled = true;
  try {
    const res = await api("/api/transactions/export?" + txQuery);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    flash("tx-status", `已导出 ${txRows.length} 条`);
  } catch (e) {
    flash("tx-status", "导出失败：" + e.message);
  }
  $("tx-export").disabled = !txRows.length;
};
for (const id of ["tx-source", "tx-from", "tx-to", "tx-account", "tx-currency", "tx-category"]) $(id).addEventListener("change", loadTransactions);
// 防误触: the table is read-only until 「修改」. Edits wait in txEdit (id -> the row as it was,
// plus the changed fields) until 「保存」 lists them for a confirm and saves them together.
// They are kept across filter changes and reloads; leaving the page with some asks first.
let txEdit = null, txLockMsg = "";
function renderTxRows() {
  $("tx-rows").innerHTML = txRows.map((t, i) => {
    const shot = t.image_id != null, mail = t.email_id != null; // manual rows have nothing to show
    const newDay = i > 0 && t.date.slice(0, 10) !== txRows[i - 1].date.slice(0, 10);
    const cls = [shot || mail ? "clickable" : "", newDay ? "newday" : ""].filter(Boolean).join(" ");
    const ed = txEdit?.get(t.id);
    const cell = (k, extra = "") => {
      if (!txEdit) return `<td>${esc(t[k])}</td>`;
      const changed = ed && k in ed;
      return `<td><input data-tx="${t.id}" data-k="${k}" value="${esc(changed ? ed[k] : t[k])}" ${extra}${changed ? ` class="changed"` : ""}></td>`;
    };
    return `<tr class="${cls}" ${shot ? `data-img-id="${t.image_id}" data-name="${esc(t.image_name)}" title="点击查看原截图"` : ""}` +
      ` ${mail ? `data-mail-id="${t.email_id}" title="点击查看原邮件"` : ""}>
    <td>${esc(t.date)}</td><td>${esc(t.weekday)}</td><td>${esc(t.account)}</td><td>${esc(t.currency)}</td>
    <td class="num ${amtClass(t.amount)}">${fmtLedger(t.amount)}</td>${cell("category", `list="m-categories"`)}${cell("description")}</tr>`;
  }).join("");
}
const txWhat = t => `${t.date.slice(0, 10)}　${fmtLedger(t.amount)} ${t.currency}${t.description ? "　" + t.description : ""}`;
// The pending edits as lines for the confirm; throws on one that can't be saved
function txChanges() {
  const out = [];
  for (const ed of txEdit?.values() ?? []) {
    if ("category" in ed) {
      if (!ed.category.trim()) throw new Error(`${txWhat(ed.t)}：种类不能为空`);
      out.push(`${txWhat(ed.t)}：种类「${ed.t.category}」→「${ed.category.trim()}」`);
    }
    if ("description" in ed) out.push(`${txWhat(ed.t)}：描述「${ed.t.description}」→「${ed.description}」`);
  }
  return out;
}
const txPending = () => { try { return txChanges().length; } catch { return 1; } };
function renderTxLock() {
  const bar = $("tx-lock");
  const msg = txLockMsg ? `<span class="lock-msg">${esc(txLockMsg)}</span>` : "";
  if (!txEdit) {
    bar.className = "lockbar";
    bar.innerHTML = `<span class="lock-state">🔒 只读</span>${msg || `<span class="muted">要改种类或描述先点「修改」，改完统一保存</span>`}` +
      `<button data-lock="edit">✎ 修改</button>`;
    return;
  }
  let n = 0, problem = "";
  try { n = txChanges().length; } catch (e) { problem = e.message; }
  bar.className = "lockbar editing";
  bar.innerHTML = `<span class="lock-state">✎ 正在修改</span>` +
    (problem ? `<span class="lock-msg warn">${esc(problem)}</span>` : msg || `<span class="muted">${n ? `${n} 处改动，还没保存` : "还没有改动"}</span>`) +
    `<button class="secondary" data-lock="cancel">${n || problem ? "放弃" : "完成"}</button>` +
    `<button data-lock="save" ${n && !problem ? "" : "disabled"}>保存${n ? `（${n} 处）` : ""}</button>`;
}
// Ends editing; with unsaved edits, asks first. False: the user wants to keep editing.
function leaveTxEdit() {
  if (!txEdit) return true;
  const n = txPending();
  if (n && !confirm(`交易有 ${n} 处改动还没保存。\n点「确定」放弃这些改动，点「取消」回去接着改。`)) return false;
  txEdit = null;
  txLockMsg = n ? "改动已放弃" : "";
  renderTxLock();
  renderTxRows();
  return true;
}
async function saveTxEdits() {
  let list;
  try { list = txChanges(); } catch (e) { txLockMsg = e.message; return renderTxLock(); }
  if (!list.length) return;
  const cats = [...txEdit.values()].filter(ed => "category" in ed).map(ed => ed.category.trim());
  const newCats = [...new Set(cats.filter(c => !knownCategories.has(c)))];
  if (!confirm(`保存这 ${list.length} 处改动？\n\n${list.map(t => "· " + t).join("\n")}` +
               (newCats.length ? `\n\n${newCats.map(c => `「${c}」`).join("")}还不在种类清单里，会一起新增。` : ""))) return;
  const edits = [...txEdit].map(([id, ed]) => ({
    id, ...("category" in ed && { category: ed.category.trim() }), ...("description" in ed && { description: ed.description }) }));
  $("tx-lock").querySelector('[data-lock="save"]').disabled = true;
  try {
    await api("/api/transactions", send("PATCH", { edits, add_categories: newCats.length > 0 }));
  } catch (e) {
    txLockMsg = "没有保存（一处也没改）：" + e.message;
    return renderTxLock();
  }
  txEdit = null;
  txLockMsg = `已保存 ${list.length} 处改动` + (newCats.length ? `，新增种类${newCats.map(c => `「${c}」`).join("")}` : "");
  renderTxLock();
  if (newCats.length) { await loadSuggestions(); fillTxFilters(); }
  await loadTransactions();
}
$("tx-lock").addEventListener("click", e => {
  const act = e.target.dataset.lock;
  if (!act) return;
  txLockMsg = "";
  if (act === "edit") { txEdit = new Map(); renderTxLock(); renderTxRows(); }
  if (act === "cancel") leaveTxEdit();
  if (act === "save") saveTxEdits();
});
$("tx-rows").addEventListener("input", e => {
  const { tx, k } = e.target.dataset;
  if (!tx || !txEdit) return;
  const id = +tx, t = txEdit.get(id)?.t ?? txRows.find(t => t.id === id);
  const ed = txEdit.get(id) ?? { t };
  if (e.target.value === t[k]) delete ed[k]; else ed[k] = e.target.value;
  if (Object.keys(ed).length > 1) txEdit.set(id, ed); else txEdit.delete(id);
  e.target.classList.toggle("changed", k in ed);
  txLockMsg = "";
  renderTxLock();
});
tableKeys($("tx-rows"), el => el.dataset.tx ? `input[data-k="${el.dataset.k}"]` : null);
window.addEventListener("beforeunload", e => { if (txEdit && txPending()) e.preventDefault(); });
renderTxLock();
$("tx-rows").addEventListener("click", e => {
  if (e.target.closest("input")) return;
  const tr = e.target.closest("tr[data-img-id]");
  if (tr) return toggleServerImage(+tr.dataset.imgId, tr.dataset.name);
  const mailTr = e.target.closest("tr[data-mail-id]");
  if (mailTr) toggleEmail(+mailTr.dataset.mailId);
});
