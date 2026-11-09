// 「收支 › 每月对比」: chosen categories month by month, and the tabs of the monthly page.

let moTab = load("tx.monthly.tab") === "trend" ? "trend" : "month";
function showMoTab(tab) {
  moTab = tab;
  save("tx.monthly.tab", tab);
  for (const b of document.querySelectorAll("[data-motab]")) b.classList.toggle("active", b.dataset.motab === tab);
  $("motab-month").hidden = tab !== "month";
  $("motab-trend").hidden = tab !== "trend";
}
// Loads whichever tab is showing (the page was opened, or the tab switched)
function loadMonthlyPage() {
  if (moTab === "trend") {
    // Each visit starts from the default categories, unless coming back from a cell's transactions
    if (!trKeepPick) trPick = null;
    trKeepPick = false;
    loadTrend(tr?.end ?? null);
  }
  else loadMonthly($("mo-month").value || null);
}
for (const b of document.querySelectorAll("[data-motab]")) b.onclick = () => { showMoTab(b.dataset.motab); loadMonthlyPage(); };
showMoTab(moTab);

const TRANSFER_NAME = "内部转账"; // as txreader/books.py TRANSFER
// Each visit compares the categories marked 固定 in settings; others are added by hand
let tr = null, trPick = null, trKeepPick = false; // trPick: category names shown, in order; null = the fixed ones
{
  const span = load("tx.trend.span");
  if (["3", "6", "12"].includes(span)) $("tr-span").value = span;
}
async function loadTrend(end) {
  // "" = all converted to the base currency; otherwise one currency as booked
  const currency = tr ? $("tr-currency").value : (load("tx.trend.currency") ?? null);
  const q = new URLSearchParams({ span: $("tr-span").value });
  if (end) q.set("end", end);
  if (currency) q.set("currency", currency);
  try { tr = await api("/api/trend?" + q); }
  catch (e) {
    // The remembered currency may have been renamed or removed: fall back to the converted view
    if (currency && e.message.includes("币种")) { save("tx.trend.currency", ""); tr = null; $("tr-currency").innerHTML = ""; return loadTrend(end); }
    return flash("tr-status", "读取失败：" + e.message);
  }
  flash("tr-status", "");
  $("tr-end").innerHTML = tr.choices.map(m => `<option value="${m}" ${m === tr.end ? "selected" : ""}>到 ${fmtMonth(m)}</option>`).join("");
  const i = tr.choices.indexOf(tr.end);
  $("tr-prev").disabled = i === tr.choices.length - 1;
  $("tr-next").disabled = i <= 0;
  const base = tr.base ?? "本位币";
  const names = tr.currency && !tr.currencies.includes(tr.currency) ? [tr.currency, ...tr.currencies] : tr.currencies;
  $("tr-currency").innerHTML = `<option value="">全部折算成${esc(base)}</option>` +
    names.map(c => `<option value="${esc(c)}" ${c === tr.currency ? "selected" : ""}>只看${esc(c)}（原币）</option>`).join("");
  renderTrend();
}

// Month headers: "7月", with the year on the first column and on each January
const trMonth = (m, i) => (i === 0 || m.endsWith("-01") ? `${m.slice(0, 4)}年` : "") + `${+m.slice(5)}月`;
// A month's cell, marked when it is off the row's average by more than a fifth
function trCell(v, avg, month, name) {
  if (v === undefined) return `<td class="num blank">·</td>`;
  if (v === null) return `<td class="missing">缺汇率</td>`;
  const now = month === tr.current;
  let cls = v < 0 ? "neg" : v === 0 ? "zero" : "", note = "";
  if (!now && avg && v !== 0 && Math.abs(v - avg) > Math.abs(avg) * 0.2) {
    const pct = Math.round((Math.abs(v) / Math.abs(avg) - 1) * 100);
    cls += pct > 0 ? " more" : " less";
    note = `比平均${avg < 0 ? (pct > 0 ? "多花" : "少花") : (pct > 0 ? "多收" : "少收")} ${Math.abs(pct)}%`;
  }
  if (now) cls += " now";
  const attrs = name ? ` data-cat="${esc(name)}" data-month="${month}"` : "";
  return `<td class="num ${cls}${name ? " clickable" : ""}"${attrs} title="${note || (name ? "点击查看这个月的交易" : "")}">${fmtAmount(v)}</td>`;
}
function trRow(r, cls = "") {
  return `<tr class="${cls}"><td>${esc(r.name)}${r.months != null ? `<span class="count" title="在 ${tr.basis.length} 个月里有 ${r.months} 个月有交易">${r.months}/${tr.basis.length}</span>` : ""}</td>` +
    tr.months.map(m => trCell(r.values[m], r.average, m, r.months != null ? r.name : null)).join("") +
    (r.average == null ? `<td class="none">—</td>` : moneyCell(r.average)) + `</tr>`;
}
// The chosen categories added up, month by month (none of them in a month: blank)
function trTotal(rows) {
  const values = {};
  for (const m of tr.months) {
    const vs = rows.map(r => r.values[m]).filter(v => v !== undefined);
    if (vs.length) values[m] = vs.includes(null) ? null : Math.round(vs.reduce((a, b) => a + b, 0) * 100) / 100;
  }
  const known = tr.basis.map(m => values[m]).filter(v => v != null);
  return { name: "所选合计", values, average: known.length ? Math.round(known.reduce((a, b) => a + b, 0) / known.length * 100) / 100 : null };
}
function renderTrend() {
  const base = tr.base ?? "本位币", unit = tr.currency ?? `折算成${base}`;
  const byName = Object.fromEntries(tr.categories.map(r => [r.name, r]));
  const defaults = tr.categories.filter(r => r.fixed).map(r => r.name);
  if (!trPick) trPick = defaults;
  const shown = trPick.filter(n => byName[n]).map(n => byName[n]);
  $("tr-warn").innerHTML = tr.missing_rates.length
    ? `<div class="warn-box">${tr.missing_rates.map(fmtMonth).join("、")}还缺外币汇率，那几个月折算不出来。去「<a href="#rates">设置 › 月汇率</a>」填上，或者改成只看某个币种。</div>` : "";

  $("tr-card").hidden = false;
  $("tr-title").textContent = `每月对比 · ${fmtMonth(tr.months[0])} – ${fmtMonth(tr.months.at(-1))}（${unit}）`;

  // Chosen categories as chips; the rest to add, the most regular first
  const rest = tr.categories.filter(r => !trPick.includes(r.name))
    .map((r, i) => [r, i]).sort((a, b) => (b[0].months - a[0].months) || (a[1] - b[1])).map(x => x[0]);
  const isDefault = trPick.join("\n") === defaults.join("\n");
  $("tr-pick").innerHTML =
    shown.map(r => `<span class="chip">${esc(r.name)}<button class="chip-x" data-drop="${esc(r.name)}" title="不比较这个种类" aria-label="去掉${esc(r.name)}">×</button></span>`).join("") +
    (rest.length ? `<select id="tr-add" aria-label="添加种类"><option value="">＋ 添加种类</option>` +
      rest.map(r => `<option value="${esc(r.name)}">${esc(r.name)}（${r.months ? `${r.months} 个月有交易` : "这段时间没有交易"}）</option>`).join("") + `</select>` : "") +
    (isDefault ? "" : `<button class="secondary" id="tr-reset" title="${defaults.length ? `恢复成固定的种类：${esc(defaults.join("、"))}` : "清空"}">恢复默认</button>`) +
    `<a class="pick-help" href="#settings" data-set-categories>在设置里改固定的种类</a>`;

  $("tr-head").innerHTML = `<tr><th>种类</th>${tr.months.map((m, i) => `<th class="num">${trMonth(m, i)}${m === tr.current ? `<span class="count">进行中</span>` : ""}</th>`).join("")}<th class="num">平均</th></tr>`;
  $("tr-rows").innerHTML = shown.map(r => trRow(r)).join("") ||
    `<tr><td colspan="${tr.months.length + 2}" class="empty">${defaults.length ? "还没选种类，用上面的「＋ 添加种类」选要对比的"
      : "还没有固定的种类：在「设置 › 种类」里勾选「固定」，以后每次打开都会显示；也可以用上面的「＋ 添加种类」临时加"}</td></tr>`;
  $("tr-foot").innerHTML = shown.length > 1 ? trRow(trTotal(shown), "strong") : "";

  $("tr-legend").innerHTML =
    `右边的 n/${tr.basis.length} 是 ${tr.basis.length} 个${tr.current ? "已经过完的" : ""}月份里有交易的月数。` +
    `「平均」只算有交易的月份${tr.current ? "，不含进行中的本月" : ""}；比平均多出两成以上标<span class="sw more"></span>，少两成以上标<span class="sw less"></span>，鼠标停在格子上看差多少。` +
    `「${esc(TRANSFER_NAME)}」不算收支，不列出来。点数字查看那个月的交易。每次打开这一页都从「固定」的种类开始。`;
}
$("tr-pick").addEventListener("click", e => {
  if (e.target.closest("[data-set-categories]")) showSetTab("categories"); // then the link opens settings
  const drop = e.target.closest("[data-drop]");
  if (drop) { trPick = trPick.filter(n => n !== drop.dataset.drop); renderTrend(); }
  if (e.target.id === "tr-reset") { trPick = null; renderTrend(); }
});
$("tr-pick").addEventListener("change", e => {
  if (e.target.id === "tr-add" && e.target.value) { trPick = [...trPick, e.target.value]; renderTrend(); }
});
// A cell opens the transactions page on that month and category
$("motab-trend").addEventListener("click", e => {
  const td = e.target.closest("td[data-cat]");
  if (!td) return;
  const m = td.dataset.month, [y, mm] = m.split("-").map(Number);
  trKeepPick = true;
  txPreset = { from: ymd(new Date(y, mm - 1, 1)), to: ymd(new Date(y, mm, 0)), category: td.dataset.cat, currency: tr.currency ?? "" };
  location.hash = "#transactions";
});
$("tr-end").addEventListener("change", e => loadTrend(e.target.value));
$("tr-prev").onclick = () => loadTrend(tr.choices[tr.choices.indexOf(tr.end) + 1]);
$("tr-next").onclick = () => loadTrend(tr.choices[tr.choices.indexOf(tr.end) - 1]);
$("tr-span").addEventListener("change", e => { save("tx.trend.span", e.target.value); loadTrend(tr?.end ?? null); });
$("tr-currency").addEventListener("change", e => { save("tx.trend.currency", e.target.value); loadTrend(tr?.end ?? null); });
