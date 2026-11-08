// 「设置 › 期初余额」: start month and opening balances.

let opening = { start_month: null, currencies: [], accounts: [] };
async function loadOpening() {
  try { opening = await api("/api/opening"); } catch (e) { return flash("op-status", "读取失败：" + e.message); }
  $("op-month").value = opening.start_month ?? "";
  // Only currencies some account uses get a column
  const cols = opening.currencies.filter(c => opening.accounts.some(a => c in a.balances));
  $("op-head").innerHTML = `<tr><th>账户</th>${cols.map(c => `<th class="num">${esc(c)}</th>`).join("")}</tr>`;
  $("op-rows").innerHTML = opening.accounts.map(a => `<tr><td>${esc(a.name)}</td>${cols.map(c => {
    if (!(c in a.balances)) return `<td class="none">—</td>`;
    const v = a.balances[c];
    return `<td class="num"><input data-op="${a.id}" data-cur="${esc(c)}" value="${v == null ? "" : fmtAmount(v)}" placeholder="0.00" inputmode="decimal"></td>`;
  }).join("")}</tr>`).join("") +
    (opening.accounts.length ? `<tr class="total"><td>合计</td>${cols.map(c => `<td class="num" data-op-total="${esc(c)}"></td>`).join("")}</tr>`
                             : `<tr><td class="empty">还没有账户</td></tr>`);
  for (const c of cols) openingTotal(c);
}
// Updated in place, so the cell being tabbed into keeps its focus
function openingTotal(cur) {
  const cell = $("op-rows").querySelector(`[data-op-total="${CSS.escape(cur)}"]`);
  if (cell) cell.textContent = fmtAmount(opening.accounts.reduce((s, a) => s + (a.balances[cur] ?? 0), 0));
}
$("op-month").addEventListener("change", async e => {
  try {
    await api("/api/opening/start-month", send("PUT", { month: e.target.value || null }));
    flash("op-status", e.target.value ? `起始月已设为 ${e.target.value}` : "已清除起始月");
  } catch (err) { flash("op-status", "保存失败：" + err.message); }
});
$("op-rows").addEventListener("change", async e => {
  const { op, cur } = e.target.dataset;
  if (!op) return;
  const text = e.target.value.trim();
  const amount = text === "" ? null : parseAmount(text);
  if (text !== "" && !/^[-+]?[\d,.\s¥]+$/.test(text)) {
    e.target.classList.add("invalid");
    return flash("op-status", `金额不对：「${text}」`);
  }
  try {
    await api(`/api/opening/${op}/${encodeURIComponent(cur)}`, send("PUT", { amount }));
    e.target.classList.remove("invalid");
    e.target.value = amount == null ? "" : fmtAmount(amount);
    opening.accounts.find(a => a.id === +op).balances[cur] = amount;
    openingTotal(cur);
    flash("op-status", "期初余额已保存");
  } catch (err) { flash("op-status", "保存失败：" + err.message); }
});
// Enter: the same currency, the next account (Shift+Enter: previous)
tableKeys($("op-rows"), el => el.dataset.op ? `input[data-cur="${CSS.escape(el.dataset.cur)}"]` : null);
