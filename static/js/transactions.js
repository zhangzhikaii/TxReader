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
  $("tx-rows").innerHTML = txRows.map((t, i) => {
    const shot = t.image_id != null, mail = t.email_id != null; // manual rows have nothing to show
    const newDay = i > 0 && t.date.slice(0, 10) !== txRows[i - 1].date.slice(0, 10);
    const cls = [shot || mail ? "clickable" : "", t.amount > 0 ? "income" : "", newDay ? "newday" : ""].filter(Boolean).join(" ");
    return `<tr class="${cls}" ${shot ? `data-img-id="${t.image_id}" data-name="${esc(t.image_name)}" title="点击查看原截图"` : ""}` +
      ` ${mail ? `data-mail-id="${t.email_id}" title="点击查看原邮件"` : ""}>
    <td>${esc(t.date)}</td><td>${esc(t.weekday)}</td><td>${esc(t.account)}</td><td>${esc(t.currency)}</td>
    <td class="num">${fmtAmount(t.amount)}</td>
    <td><input data-tx="${t.id}" data-k="category" value="${esc(t.category)}" list="m-categories"></td>
    <td><input data-tx="${t.id}" data-k="description" value="${esc(t.description)}"></td></tr>`;
  }).join("");
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
$("tx-rows").addEventListener("change", async e => {
  const { tx: id, k } = e.target.dataset;
  if (!id) return;
  const value = e.target.value;
  const t = txRows.find(t => t.id === +id);
  if (k === "category" && !value.trim()) {
    e.target.value = t?.category ?? "";
    return flash("tx-status", "种类不能为空");
  }
  const save = body => api(`/api/transactions/${id}`, send("PATCH", body));
  try {
    if (k === "category") {
      // Changing a stored transaction's category is confirmed; a new category is added to the list
      const name = value.trim(), old = t?.category ?? "";
      if (name === old) return;
      const isNew = !knownCategories.has(name);
      const what = t ? `${t.date.slice(0, 10)}　${fmtAmount(t.amount)} ${t.currency}${t.description ? "　" + t.description : ""}` : "";
      if (!confirm(`把这笔交易的种类从「${old}」改为「${name}」？\n${what}` +
                   (isNew ? `\n\n「${name}」还不在种类清单里，会一起新增。` : ""))) {
        e.target.value = old;
        return flash("tx-status", "没有修改");
      }
      await save({ category: name, add_category: isNew });
      if (t) t.category = name;
      e.target.value = name;
      if (isNew) { await loadSuggestions(); fillTxFilters(); }
      return flash("tx-status", isNew ? `已新增种类「${name}」，这笔交易已改为「${name}」` : `种类已改为「${name}」`);
    }
    await save({ [k]: value });
    if (t) t[k] = value;
    flash("tx-status", "描述已保存");
  } catch (err) {
    if (k === "category") e.target.value = t?.category ?? "";
    flash("tx-status", "保存失败：" + err.message);
  }
});
$("tx-rows").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.isComposing && e.target.dataset.tx) e.target.blur();
});
$("tx-rows").addEventListener("click", e => {
  if (e.target.closest("input")) return;
  const tr = e.target.closest("tr[data-img-id]");
  if (tr) return toggleServerImage(+tr.dataset.imgId, tr.dataset.name);
  const mailTr = e.target.closest("tr[data-mail-id]");
  if (mailTr) toggleEmail(+mailTr.dataset.mailId);
});
