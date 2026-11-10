// 「收支」: one month's (or year's) income and expense by category and currency.

let mo = null;
{
  const v = load("tx.monthly.hideEmpty");
  if (v !== null) $("mo-hide-empty").checked = v === "1";
  const sort = load("tx.monthly.sort");
  if (["list", "spend", "count"].includes(sort)) $("mo-sort").value = sort;
}
// Category order: as in settings (the server's order), biggest spending first (by the converted
// total; ones that can't be converted go last), or most transactions first. Ties keep list order.
function sortCategories(rows) {
  const by = $("mo-sort").value;
  if (by === "list") return rows;
  const key = by === "count" ? r => -r.count : r => r.converted ?? Infinity;
  return rows.map((r, i) => [r, i]).sort((a, b) => (key(a[0]) - key(b[0])) || (a[1] - b[1])).map(x => x[0]);
}
// Period bar: a month or a year, stepped with ‹ › within the months there are data for.
// The page opens on the period containing today; after that it keeps what was chosen.
let moPeriod = load("tx.monthly.period") === "year" ? "year" : "month";
$("mo-period").value = moPeriod;
const moToday = () => { const t = ymd(new Date()); return moPeriod === "year" ? t.slice(0, 4) : t.slice(0, 7); };
const moKey = () => mo ? (mo.period === "year" ? mo.year : mo.month) : moToday();
const moLabel = () => mo.period === "year" ? `${mo.year}年` : fmtMonth(mo.month);
async function loadMonthly(key) {
  key = key ?? moToday();
  try { mo = await api("/api/monthly?" + (key.length === 4 ? "year=" : "month=") + key); }
  catch (e) { return flash("mo-status", "读取失败：" + e.message); }
  flash("mo-status", "");
  const choices = mo.period === "year" ? mo.years : mo.months, i = choices.indexOf(moKey());
  $("mo-range").textContent = moLabel();
  $("mo-prev").disabled = i === choices.length - 1;
  $("mo-next").disabled = i <= 0;
  renderMonthly();
}
$("mo-period").addEventListener("change", e => {
  const key = moKey();
  moPeriod = e.target.value;
  save("tx.monthly.period", moPeriod);
  // Month -> its year; year -> its latest month there is (this month in the current year)
  if (moPeriod === "year") return loadMonthly(key.slice(0, 4));
  const months = mo?.months.filter(m => m.startsWith(key)) ?? [];
  loadMonthly(months.includes(moToday()) ? moToday() : months[0]);
});
$("mo-today").onclick = () => loadMonthly(moToday());
function renderMonthly() {
  const cur = mo.currencies, base = mo.base ?? "本位币";
  // Columns: currencies with something this month, or an FX gain
  const cols = cur.filter(c => mo.totals[c] || mo.categories.some(r => c in r.amounts) || (mo.fx_gain?.[c] ?? 0) !== 0 || c === mo.base);
  const label = moLabel(), missing = Object.entries(mo.missing_rates).filter(([c]) => cols.includes(c));
  const missingText = missing.map(([c, ms]) => mo.period === "year" ? `${esc(c)}（${ms.map(m => `${+m.slice(5)}月`).join("、")}）` : esc(c)).join("、");
  $("mo-warn").innerHTML =
    (missing.length ? `<div class="warn-box">${mo.period === "year" ? `${label}还有月份没填汇率：${missingText}` : `${label}还没填 ${missingText} 的汇率`}，折算不出来。去「<a href="#rates">设置 › 月汇率</a>」填上。</div>` : "") +
    (mo.check === "diff" ? `<div class="warn-box">和结余页对不上：这里的${esc(base)}合计是 ${fmtAmount(mo.check_total)}，结余页的月度差额是 ${fmtAmount(mo.balance_change)}` +
      (mo.period === "year" ? `（对不上的月份：${mo.check_diff.map(fmtMonth).join("、")}）` : "") + `。可能是期初余额或某个月的汇率有问题。</div>` : "");

  // The card: this month in the base currency = income/expense converted + FX gain
  const total = mo.base_grand_total ?? (mo.fx_gain ? null : mo.converted_total);
  const parts = [`<span class="part">收支 <b>${mo.converted_total == null ? "缺汇率" : signed(mo.converted_total)}</b></span>`,
    `<span class="part">汇差 <b>${mo.fx_gain ? (mo.fx_gain_total == null ? "缺汇率" : signed(mo.fx_gain_total)) : "—"}</b></span>`];
  if (mo.check === "ok") parts.push(`<span class="check ok">✓ 勾稽无误</span>`);
  if (mo.check === "diff") parts.push(`<span class="check diff">⚠ 勾稽有误，差额： ${fmtAmount(mo.check_total - mo.balance_change)}</span>`);
  $("mo-hero").innerHTML = `<div class="hero">
    <div class="label">${esc(base)}合计 · ${label}${mo.fx_note ? `<span class="muted">（${esc(mo.fx_note)}）</span>` : ""}</div>
    <div class="row">
      ${total == null ? `<span class="missing">缺汇率，算不出来</span>`
                      : `<span class="total ${total < 0 ? "neg" : ""}">${signed(total)}<span class="unit">${esc(base)}</span></span>`}
      <span class="parts">${parts.join("")}</span>
    </div></div>`;

  $("mo-card").hidden = false;
  $("mo-title").textContent = `${label} 收支`;
  $("mo-head").innerHTML = `<tr><th>种类</th>${cols.map(c => `<th class="num">${esc(c)}</th>`).join("")}<th class="num">合计（${esc(base)}）</th></tr>`;
  const rows = sortCategories($("mo-hide-empty").checked ? mo.categories.filter(r => r.count) : mo.categories);
  // The category name (when it has transactions) opens them on the transactions page;
  // number cells don't, so they can be selected and copied
  $("mo-rows").innerHTML = rows.map(r => `<tr><td>` +
    (r.count ? `<span class="jump" data-cat="${esc(r.name)}" title="在交易页查看">${esc(r.name)}</span><span class="count">${r.count} 笔</span>` : esc(r.name)) + `</td>` +
    cols.map(c => c in r.amounts ? moneyCell(r.amounts[c]) : `<td class="num blank">·</td>`).join("") +
    (Object.keys(r.amounts).length ? moneyCell(r.converted) : `<td class="num blank">·</td>`) + `</tr>`).join("") ||
    `<tr><td colspan="${cols.length + 2}" class="empty">${mo.period === "year" ? "这一年" : "这个月"}没有交易</td></tr>`;
  const fx = c => mo.fx_gain ? (c === mo.base ? `<td class="none">—</td>` : moneyCell(mo.fx_gain[c])) : `<td class="none">—</td>`;
  $("mo-foot").innerHTML =
    `<tr class="strong"><td>本位币合计</td>${cols.map(c => moneyCell(mo.totals[c])).join("")}${moneyCell(mo.converted_total)}</tr>` +
    `<tr><td>汇差盈亏</td>${cols.map(fx).join("")}${mo.fx_gain ? moneyCell(mo.fx_gain_total) : `<td class="none">—</td>`}</tr>` +
    `<tr class="strong"><td>${esc(base)}合计</td>${cols.map(c => moneyCell(mo.base_total[c])).join("")}${total == null ? `<td class="missing">缺汇率</td>` : moneyCell(total)}</tr>`;
}
$("mo-rows").addEventListener("click", e => {
  const el = e.target.closest("[data-cat]");
  if (!el) return;
  const [y, m] = mo.period === "year" ? [+mo.year, 0] : mo.month.split("-").map(Number);
  const [a, b] = mo.period === "year" ? [new Date(y, 0, 1), new Date(y, 11, 31)] : [new Date(y, m - 1, 1), new Date(y, m, 0)];
  txPreset = { from: ymd(a), to: ymd(b), category: el.dataset.cat, currency: "" };
  location.hash = "#transactions";
});
for (const [id, n] of [["mo-prev", 1], ["mo-next", -1]]) $(id).onclick = () => {
  const choices = mo.period === "year" ? mo.years : mo.months; // newest first
  loadMonthly(choices[choices.indexOf(moKey()) + n]);
};
$("mo-hide-empty").addEventListener("change", e => { save("tx.monthly.hideEmpty", e.target.checked ? "1" : "0"); if (mo) renderMonthly(); });
$("mo-sort").addEventListener("change", e => { save("tx.monthly.sort", e.target.value); if (mo) renderMonthly(); });
