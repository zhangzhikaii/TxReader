// 「收支」: one month's income and expense by category and currency.

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
async function loadMonthly(month) {
  try { mo = await api("/api/monthly" + (month ? "?month=" + month : "")); }
  catch (e) { return flash("mo-status", "读取失败：" + e.message); }
  flash("mo-status", "");
  $("mo-month").innerHTML = mo.months.map(m => `<option value="${m}" ${m === mo.month ? "selected" : ""}>${fmtMonth(m)}</option>`).join("");
  const i = mo.months.indexOf(mo.month);
  $("mo-prev").disabled = i === mo.months.length - 1;
  $("mo-next").disabled = i <= 0;
  renderMonthly();
}
function renderMonthly() {
  const cur = mo.currencies, base = mo.base ?? "本位币";
  // Columns: currencies with something this month, or an FX gain
  const cols = cur.filter(c => mo.totals[c] || mo.categories.some(r => c in r.amounts) || (mo.fx_gain?.[c] ?? 0) !== 0 || c === mo.base);
  const noRate = cols.filter(c => mo.rates[c] == null && mo.totals[c]);
  $("mo-warn").innerHTML =
    (noRate.length ? `<div class="warn-box">${fmtMonth(mo.month)}还没填 ${noRate.map(esc).join("、")} 的汇率，折算不出来。去「<a href="#rates">设置 › 月汇率</a>」填上。</div>` : "") +
    (mo.check === "diff" ? `<div class="warn-box">和结余页对不上：这里的${esc(base)}合计是 ${fmtAmount(mo.base_grand_total)}，结余页的月度差额是 ${fmtAmount(mo.balance_change)}。可能是期初余额或某个月的汇率有问题。</div>` : "");

  // The card: this month in the base currency = income/expense converted + FX gain
  const total = mo.base_grand_total ?? (mo.fx_gain ? null : mo.converted_total);
  const parts = [`<span class="part">收支 <b>${mo.converted_total == null ? "缺汇率" : signed(mo.converted_total)}</b></span>`,
    `<span class="part">汇差 <b>${mo.fx_gain ? (mo.fx_gain_total == null ? "缺汇率" : signed(mo.fx_gain_total)) : "—"}</b></span>`];
  if (mo.check === "ok") parts.push(`<span class="check ok">✓ 勾稽无误</span>`);
  if (mo.check === "diff") parts.push(`<span class="check diff">⚠ 勾稽有误，差额： ${fmtAmount(mo.base_grand_total - mo.balance_change)}</span>`);
  $("mo-hero").innerHTML = `<div class="hero">
    <div class="label">${esc(base)}合计 · ${fmtMonth(mo.month)}${mo.fx_note ? `<span class="muted">（${esc(mo.fx_note)}）</span>` : ""}</div>
    <div class="row">
      ${total == null ? `<span class="missing">缺汇率，算不出来</span>`
                      : `<span class="total ${total < 0 ? "neg" : ""}">${signed(total)}<span class="unit">${esc(base)}</span></span>`}
      <span class="parts">${parts.join("")}</span>
    </div></div>`;

  $("mo-card").hidden = false;
  $("mo-title").textContent = `${fmtMonth(mo.month)} 收支`;
  $("mo-head").innerHTML = `<tr><th>种类</th>${cols.map(c => `<th class="num">${esc(c)}</th>`).join("")}<th class="num">合计（${esc(base)}）</th></tr>`;
  const rows = sortCategories($("mo-hide-empty").checked ? mo.categories.filter(r => r.count) : mo.categories);
  // The category name (when it has transactions) opens them on the transactions page;
  // number cells don't, so they can be selected and copied
  $("mo-rows").innerHTML = rows.map(r => `<tr><td>` +
    (r.count ? `<span class="jump" data-cat="${esc(r.name)}" title="在交易页查看">${esc(r.name)}</span><span class="count">${r.count} 笔</span>` : esc(r.name)) + `</td>` +
    cols.map(c => c in r.amounts ? moneyCell(r.amounts[c]) : `<td class="num blank">·</td>`).join("") +
    (Object.keys(r.amounts).length ? moneyCell(r.converted) : `<td class="num blank">·</td>`) + `</tr>`).join("") ||
    `<tr><td colspan="${cols.length + 2}" class="empty">这个月没有交易</td></tr>`;
  const fx = c => mo.fx_gain ? (c === mo.base ? `<td class="none">—</td>` : moneyCell(mo.fx_gain[c])) : `<td class="none">—</td>`;
  $("mo-foot").innerHTML =
    `<tr class="strong"><td>本位币合计</td>${cols.map(c => moneyCell(mo.totals[c])).join("")}${moneyCell(mo.converted_total)}</tr>` +
    `<tr><td>汇差盈亏</td>${cols.map(fx).join("")}${mo.fx_gain ? moneyCell(mo.fx_gain_total) : `<td class="none">—</td>`}</tr>` +
    `<tr class="strong"><td>${esc(base)}合计</td>${cols.map(c => moneyCell(mo.base_total[c])).join("")}${total == null ? `<td class="missing">缺汇率</td>` : moneyCell(total)}</tr>`;
}
$("mo-rows").addEventListener("click", e => {
  const el = e.target.closest("[data-cat]");
  if (!el) return;
  const [y, m] = mo.month.split("-").map(Number);
  txPreset = { from: ymd(new Date(y, m - 1, 1)), to: ymd(new Date(y, m, 0)), category: el.dataset.cat, currency: "" };
  location.hash = "#transactions";
});
$("mo-month").addEventListener("change", e => loadMonthly(e.target.value));
$("mo-prev").onclick = () => loadMonthly(mo.months[mo.months.indexOf(mo.month) + 1]);
$("mo-next").onclick = () => loadMonthly(mo.months[mo.months.indexOf(mo.month) - 1]);
$("mo-hide-empty").addEventListener("change", e => { save("tx.monthly.hideEmpty", e.target.checked ? "1" : "0"); if (mo) renderMonthly(); });
$("mo-sort").addEventListener("change", e => { save("tx.monthly.sort", e.target.value); if (mo) renderMonthly(); });
