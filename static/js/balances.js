// 「结余」: month-end balances of every account and currency.

let bal = null;
{
  const v = load("tx.balances.hideQuiet");
  if (v !== null) $("bal-hide-quiet").checked = v === "1";
}
async function loadBalances(month) {
  try { bal = await api("/api/balances" + (month ? "?month=" + month : "")); }
  catch (e) { return flash("bal-status", "读取失败：" + e.message); }
  flash("bal-status", "");
  $("bal-month").innerHTML = bal.months.map(m => `<option value="${m}" ${m === bal.month ? "selected" : ""}>${fmtMonth(m)}</option>`).join("");
  const i = bal.months.indexOf(bal.month);
  $("bal-prev").disabled = !bal.month || i === bal.months.length - 1;
  $("bal-next").disabled = !bal.month || i <= 0;

  if (!bal.month) {
    $("bal-card").hidden = true;
    $("bal-tiles").innerHTML = "";
    $("bal-warn").innerHTML = `<div class="warn-box">还没有设起始月。先去「<a href="#settings">设置 › 期初余额</a>」选起始月，填好那个月底各账户的余额。</div>`;
    return;
  }
  const cur = bal.currencies, base = bal.base ?? "本位币";
  const noRate = cur.filter(c => bal.rates[c] == null);
  $("bal-warn").innerHTML = noRate.length
    ? `<div class="warn-box">${fmtMonth(bal.month)}还没填 ${noRate.map(esc).join("、")} 的汇率，折算不出来。去「<a href="#rates">设置 › 月汇率</a>」填上。</div>` : "";
  $("bal-card").hidden = false;
  $("bal-title").textContent = monthEnd(bal.month) + " 结余";
  $("bal-head").innerHTML = `<tr><th>账户</th>${cur.map(c => `<th class="num">${esc(c)}</th>`).join("")}</tr>`;
  renderBalanceRows();
  const first = bal.month === bal.start_month;
  $("bal-foot").innerHTML =
    `<tr class="strong"><td>合计（原币）</td>${cur.map(c => moneyCell(bal.totals[c])).join("")}</tr>` +
    `<tr class="strong"><td>折算成${esc(base)}</td>${cur.map(c => moneyCell(bal.converted[c])).join("")}</tr>` +
    `<tr><td>汇差盈亏</td>${cur.map(c => first ? `<td class="none">—</td>` : moneyCell(bal.fx_gain[c], "缺汇率")).join("")}</tr>`;

  // One card: the total in the base currency, and how much it moved since last month
  let delta;
  if (first) {
    delta = `<span class="chg na">起始月，没有上月可比</span>`;
  } else if (bal.change == null) {
    delta = `<span class="chg na">较上月：${bal.prev_total == null ? "上月缺汇率" : "缺汇率"}</span>`;
  } else {
    const dir = bal.change > 0 ? "up" : bal.change < 0 ? "down" : "flat";
    const pct = bal.prev_total ? ` <span class="pct">${bal.change > 0 ? "+" : ""}${(bal.change / Math.abs(bal.prev_total) * 100).toFixed(2)}%</span>` : "";
    delta = `<span class="chg ${dir}">${{ up: "▲", down: "▼", flat: "＝" }[dir]} ${fmtAmount(Math.abs(bal.change))}${pct}</span>` +
            `<span class="vs">较上月（上月 ${fmtAmount(bal.prev_total)}）</span>`;
  }
  $("bal-tiles").innerHTML = `<div class="hero">
    <div class="label">${esc(base)}合计 · ${monthEnd(bal.month)}</div>
    <div class="row">
      ${bal.total == null ? `<span class="missing">缺汇率，算不出来</span>`
                          : `<span class="total ${bal.total < 0 ? "neg" : ""}">${fmtAmount(bal.total)}<span class="unit">${esc(base)}</span></span>`}
      <span class="delta">${delta}</span>
    </div></div>`;
}
$("bal-month").addEventListener("change", e => loadBalances(e.target.value));
$("bal-prev").onclick = () => { const i = bal.months.indexOf(bal.month); loadBalances(bal.months[i + 1]); };
$("bal-next").onclick = () => { const i = bal.months.indexOf(bal.month); loadBalances(bal.months[i - 1]); };
// Account rows; the totals below always count every account
function renderBalanceRows() {
  const cur = bal.currencies;
  const quiet = a => !a.count && Object.values(a.balances).every(v => !v);
  const rows = $("bal-hide-quiet").checked ? bal.accounts.filter(a => !quiet(a)) : bal.accounts;
  const hidden = bal.accounts.length - rows.length;
  $("bal-rows").innerHTML = rows.map(a =>
    `<tr><td>${esc(a.name)}${a.count ? `<span class="count">${a.count} 笔</span>` : ""}</td>${cur.map(c => moneyCell(a.balances[c])).join("")}</tr>`).join("") ||
    `<tr><td colspan="${cur.length + 1}" class="empty">${bal.accounts.length ? "这个月没有交易、也没有余额的账户都隐藏了" : "还没有账户"}</td></tr>`;
  flash("bal-status", hidden ? `隐藏了 ${hidden} 个账户` : "");
}
$("bal-hide-quiet").addEventListener("change", e => {
  save("tx.balances.hideQuiet", e.target.checked ? "1" : "0");
  if (bal?.month) renderBalanceRows();
});
