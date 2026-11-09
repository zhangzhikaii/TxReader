// 「设置 › 期初余额」: start month and opening balances (locked until 「✎ 修改」, see settings.js).

let opening = { start_month: null, currencies: [], accounts: [] };
let opEdit = null; // while editing: { start (typed, or undefined), cells: {"accountId|currency": typed text} }
async function loadOpening() {
  try { opening = await api("/api/opening"); } catch (e) { return flash("op-status", "读取失败：" + e.message); }
  renderOpening();
}
const opKey = (id, cur) => `${id}|${cur}`;
const opValid = text => text === "" || /^[-+]?[\d,.\s¥]+$/.test(text);
// The balance as it would be saved: what was typed (when valid), else what is stored
function opValue(a, cur) {
  const text = opEdit?.cells[opKey(a.id, cur)]?.trim();
  if (text === undefined || !opValid(text)) return a.balances[cur];
  return text === "" ? null : parseAmount(text);
}
function renderOpening() {
  $("op-month").value = opEdit?.start ?? opening.start_month ?? "";
  $("op-month").disabled = !opEdit;
  // Only currencies some account uses get a column
  const cols = opening.currencies.filter(c => opening.accounts.some(a => c in a.balances));
  $("op-head").innerHTML = `<tr><th>账户</th>${cols.map(c => `<th class="num">${esc(c)}</th>`).join("")}</tr>`;
  $("op-rows").innerHTML = opening.accounts.map(a => `<tr><td>${esc(a.name)}</td>${cols.map(c => {
    if (!(c in a.balances)) return `<td class="none">—</td>`;
    const v = a.balances[c];
    if (!opEdit) return `<td class="num">${v == null ? "" : fmtAmount(v)}</td>`;
    const typed = opEdit.cells[opKey(a.id, c)];
    return `<td class="num"><input data-op="${a.id}" data-cur="${esc(c)}" value="${esc(typed ?? (v == null ? "" : fmtAmount(v)))}"` +
      ` class="${typed !== undefined && !opValid(typed.trim()) ? "invalid" : ""}" placeholder="0.00" inputmode="decimal"></td>`;
  }).join("")}</tr>`).join("") +
    (opening.accounts.length ? `<tr class="total"><td>合计</td>${cols.map(c => `<td class="num" data-op-total="${esc(c)}"></td>`).join("")}</tr>`
                             : `<tr><td class="empty">还没有账户</td></tr>`);
  for (const c of cols) openingTotal(c);
}
// Updated in place, so the cell being typed in keeps its focus
function openingTotal(cur) {
  const cell = $("op-rows").querySelector(`[data-op-total="${CSS.escape(cur)}"]`);
  if (cell) cell.textContent = fmtAmount(opening.accounts.reduce((s, a) => s + (opValue(a, cur) ?? 0), 0));
}
const opShow = v => v == null ? "（空）" : fmtAmount(v);
function openingChanges() {
  const out = [];
  if (opEdit.start !== undefined && (opEdit.start || null) !== opening.start_month) {
    out.push({ text: `起始月：${opening.start_month ?? "未设"} → ${opEdit.start || "清除"}`, start: opEdit.start || null });
  }
  for (const a of opening.accounts) for (const cur of Object.keys(a.balances)) {
    const text = opEdit.cells[opKey(a.id, cur)]?.trim();
    if (text === undefined) continue;
    if (!opValid(text)) throw new Error(`「${a.name}」${cur}的金额不对：「${text}」`);
    const amount = opValue(a, cur), old = a.balances[cur] ?? null;
    if (amount === old || (amount != null && old != null && Math.abs(amount - old) < 0.005)) continue;
    out.push({ text: `「${a.name}」${cur}：${opShow(old)} → ${opShow(amount)}`, cell: { account_id: a.id, currency: cur, amount } });
  }
  return out;
}
lockable("opening", {
  label: "期初余额",
  editing: () => !!opEdit,
  begin() { opEdit = { start: undefined, cells: {} }; renderOpening(); },
  end() { opEdit = null; renderOpening(); },
  changes: () => openingChanges().map(c => c.text),
  save() {
    const changes = openingChanges(), body = { balances: changes.filter(c => c.cell).map(c => c.cell) };
    const start = changes.find(c => "start" in c);
    if (start) body.start_month = start.start;
    return api("/api/opening", send("PUT", body));
  },
  reload: loadOpening,
});
$("op-month").addEventListener("input", e => {
  if (!opEdit) return;
  opEdit.start = e.target.value;
  renderLock();
});
$("op-rows").addEventListener("input", e => {
  const { op, cur } = e.target.dataset;
  if (!op || !opEdit) return;
  opEdit.cells[opKey(op, cur)] = e.target.value;
  e.target.classList.toggle("invalid", !opValid(e.target.value.trim()));
  openingTotal(cur);
  lockMsg = "";
  renderLock();
});
// Enter: the same currency, the next account (Shift+Enter: previous)
tableKeys($("op-rows"), el => el.dataset.op ? `input[data-cur="${CSS.escape(el.dataset.cur)}"]` : null);
