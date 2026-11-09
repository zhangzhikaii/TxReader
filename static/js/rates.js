// 「设置 › 月汇率」: one rate per month and foreign currency (locked until 「✎ 修改」, see settings.js).

let fx = { base: null, currencies: [], months: [], rates: {} };
let fxEdit = null; // while editing: {"month|currency": typed text}
async function loadRates() {
  try { fx = await api("/api/rates"); } catch (e) { return flash("fx-status", "读取失败：" + e.message); }
  renderRates();
}
// null: cleared; NaN: not a valid rate
const fxParse = text => text === "" ? null : (Number(text.replace(/[,\s]/g, "")) > 0 ? Number(text.replace(/[,\s]/g, "")) : NaN);
function renderRates() {
  $("fx-help").textContent = fx.base
    ? `每月填一次：1 单位外币 = 多少${fx.base}。${fx.base}是本位币，固定为 1，不用填。`
    : "还没有本位币（人民币），先在「设置」的币种清单里添加「人民币」。";
  $("fx-head").innerHTML = `<tr><th>月份</th>${fx.currencies.map(c => `<th class="num">1 ${esc(c)} =</th>`).join("")}</tr>`;
  $("fx-rows").innerHTML = fx.currencies.length ? fx.months.map(m => `<tr><td>${fmtMonth(m)}</td>${fx.currencies.map(c => {
    const v = fx.rates[m]?.[c];
    if (!fxEdit) return `<td class="num ${v == null ? "missing" : ""}">${v ?? ""}</td>`;
    const typed = fxEdit[`${m}|${c}`];
    const shown = typed ?? (v ?? "");
    const bad = typed !== undefined && Number.isNaN(fxParse(typed.trim()));
    return `<td class="num"><input data-fx="${m}" data-cur="${esc(c)}" value="${esc(shown)}" class="${bad ? "invalid" : shown === "" ? "missing" : ""}" inputmode="decimal"></td>`;
  }).join("")}</tr>`).join("") : `<tr><td class="empty">除了本位币还没有别的币种</td></tr>`;
}
function rateChanges() {
  const out = [];
  for (const [key, typed] of Object.entries(fxEdit)) {
    const [month, cur] = key.split("|"), text = typed.trim(), rate = fxParse(text);
    if (Number.isNaN(rate)) throw new Error(`${fmtMonth(month)}${cur}的汇率要是大于 0 的数字：「${text}」`);
    const old = fx.rates[month]?.[cur] ?? null;
    if (rate === old) continue;
    out.push({ text: `${fmtMonth(month)} ${cur}：${old ?? "（空）"} → ${rate ?? "清空"}`, cell: { month, currency: cur, rate } });
  }
  return out.sort((a, b) => b.cell.month.localeCompare(a.cell.month)); // newest first, as in the table
}
lockable("rates", {
  label: "月汇率",
  editing: () => !!fxEdit,
  begin() { fxEdit = {}; renderRates(); },
  end() { fxEdit = null; renderRates(); },
  changes: () => rateChanges().map(c => c.text),
  save: () => api("/api/rates", send("PUT", { rates: rateChanges().map(c => c.cell) })),
  reload: loadRates,
});
$("fx-rows").addEventListener("input", e => {
  const { fx: month, cur } = e.target.dataset;
  if (!month || !fxEdit) return;
  fxEdit[`${month}|${cur}`] = e.target.value;
  const rate = fxParse(e.target.value.trim());
  e.target.classList.toggle("invalid", Number.isNaN(rate));
  e.target.classList.toggle("missing", rate === null);
  lockMsg = "";
  renderLock();
});
// Enter: the same currency, the month below (older); Shift+Enter: above
tableKeys($("fx-rows"), el => el.dataset.fx ? `input[data-cur="${CSS.escape(el.dataset.cur)}"]` : null);
