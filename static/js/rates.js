// 「设置 › 月汇率」: one rate per month and foreign currency.

let fx = { base: null, currencies: [], months: [], rates: {} };
async function loadRates() {
  try { fx = await api("/api/rates"); } catch (e) { return flash("fx-status", "读取失败：" + e.message); }
  $("fx-help").textContent = fx.base
    ? `每月填一次：1 单位外币 = 多少${fx.base}。${fx.base}是本位币，固定为 1，不用填。`
    : "还没有本位币（人民币），先在「设置」的币种清单里添加「人民币」。";
  $("fx-head").innerHTML = `<tr><th>月份</th>${fx.currencies.map(c => `<th class="num">1 ${esc(c)} =</th>`).join("")}</tr>`;
  $("fx-rows").innerHTML = fx.currencies.length ? fx.months.map(m => `<tr><td>${fmtMonth(m)}</td>${fx.currencies.map(c => {
    const v = fx.rates[m]?.[c];
    return `<td class="num"><input data-fx="${m}" data-cur="${esc(c)}" value="${v ?? ""}" class="${v == null ? "missing" : ""}" inputmode="decimal"></td>`;
  }).join("")}</tr>`).join("") : `<tr><td class="empty">除了本位币还没有别的币种</td></tr>`;
}
$("fx-rows").addEventListener("change", async e => {
  const { fx: month, cur } = e.target.dataset;
  if (!month) return;
  const text = e.target.value.trim();
  const rate = text === "" ? null : Number(text.replace(/[,\s]/g, ""));
  if (rate !== null && !(rate > 0)) {
    e.target.classList.add("invalid");
    return flash("fx-status", `汇率要是大于 0 的数字：「${text}」`);
  }
  try {
    await api(`/api/rates/${month}/${encodeURIComponent(cur)}`, send("PUT", { rate }));
    e.target.classList.remove("invalid");
    e.target.classList.toggle("missing", rate === null);
    flash("fx-status", rate === null ? `已清空 ${fmtMonth(month)} 的${cur}汇率` : `${fmtMonth(month)}：1 ${cur} = ${rate} ${fx.base ?? ""}`);
    (fx.rates[month] ??= {})[cur] = rate;
  } catch (err) { flash("fx-status", "保存失败：" + err.message); }
});
// Enter: the same currency, the month below (older); Shift+Enter: above
tableKeys($("fx-rows"), el => el.dataset.fx ? `input[data-cur="${CSS.escape(el.dataset.cur)}"]` : null);
