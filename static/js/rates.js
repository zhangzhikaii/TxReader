// 「设置 › 月汇率」: one rate per month and foreign currency (locked until 「✎ 修改」, see settings.js).
// Currencies with a code get their rates from Yahoo Finance (server side, txreader/fx.py): synced
// once when the tab is shown (loadSettings resets fxSynced), 「⟳ 更新汇率」 refetches this month's.
// Opening 结余 / 收支 syncs too (fxSyncFor).

let fx = { base: null, base_code: "", currencies: [], codes: {}, months: [], rates: {}, sources: {}, this_month: "" };
let fxEdit = null; // while editing: {"month|currency": typed text}
let fxSynced = false;
let fxMsg = "";    // what the last fetch said
async function loadRates() {
  try { fx = await api("/api/rates"); } catch (e) { return flash("fx-status", "读取失败：" + e.message); }
  renderRates();
  fxAutoSync();
}
const fxAuto = c => !!(fx.base_code && fx.codes[c]);
// Called when the rates tab is shown (settings.js) and after loading
function fxAutoSync() {
  if (fxSynced || fxEdit || $("view-settings").hidden || setTab !== "rates" || !fx.currencies.some(fxAuto)) return;
  fxSync("/api/rates/sync", "正在从 Yahoo Finance 取汇率…");
}
async function fxSync(url, busy) {
  fxSynced = true;
  flash("fx-status", busy);
  $("fx-refresh").disabled = true;
  let r;
  try {
    r = await api(url, send("POST", {}));
    fxMsg = (url.endsWith("refresh") ? (r.updated ? `更新了 ${r.updated} 个汇率` : "没有可更新的汇率") : "") +
      (r.warnings.length ? (url.endsWith("refresh") ? "；" : "") + "有些汇率没取到：" + r.warnings.join("；") : "");
  } catch (e) {
    fxMsg = "取汇率失败：" + e.message;
  }
  if (r?.updated && !fxEdit) {
    try { fx = await api("/api/rates"); } catch {}
  }
  renderRates();
}
// 结余 / 收支 pages: fetch what is missing or stale when the page is opened (main.js), and show
// the page again if anything came. Quietly: the pages already say which rates are missing.
async function fxSyncFor(view, reload) {
  let r;
  try { r = await api("/api/rates/sync", send("POST", {})); } catch { return; }
  if (r.updated && !$("view-" + view).hidden) reload();
}
$("fx-refresh").onclick = () => fxSync("/api/rates/refresh", "正在更新汇率…");

// null: cleared; NaN: not a valid rate
const fxParse = text => text === "" ? null : (Number(text.replace(/[,\s]/g, "")) > 0 ? Number(text.replace(/[,\s]/g, "")) : NaN);
const fxNote = (m, c) => {
  const s = fx.sources[m]?.[c];
  if (!s) return fx.rates[m]?.[c] != null && fxAuto(c) ? "手填" : "";
  if (!s.at) return "还没取到";
  if (s.source === "close") return `${+s.at.slice(5, 7)}/${+s.at.slice(8, 10)} 收盘`;
  const d = new Date(s.at);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())} 最新`;
};
function renderRates() {
  const auto = fx.currencies.filter(fxAuto);
  $("fx-help").textContent = !fx.base
    ? "还没有本位币（人民币），先在「设置」的币种清单里添加「人民币」。"
    : `1 单位外币 = 多少${fx.base}。${fx.base}是本位币，固定为 1，不用填。` +
      (!fx.base_code ? `本位币没有代码，汇率都要手填（在「币种」标签里给${fx.base}填上 CNY 就能自动取）。`
        : `有代码的币种从 Yahoo Finance 自动取：过去的月份取月底最后一个交易日的收盘价，本月取最新价；` +
          `手填的不会被覆盖。没有代码的币种手填（代码在「币种」标签里设）。`);
  $("fx-head").innerHTML = `<tr><th>月份</th>${fx.currencies.map(c => `<th class="num">1 ${esc(c)} =` +
    `<span class="price-note">${fxAuto(c) ? `${esc(fx.codes[c])}${esc(fx.base_code)}=X` : "手填"}</span></th>`).join("")}</tr>`;
  $("fx-rows").innerHTML = fx.currencies.length ? fx.months.map(m => `<tr><td>${fmtMonth(m)}</td>${fx.currencies.map(c => {
    const v = fx.rates[m]?.[c], note = fxNote(m, c);
    if (!fxEdit) return `<td class="num ${v == null ? "missing" : ""}">${v ?? ""}${note ? `<span class="price-note">${note}</span>` : ""}</td>`;
    const typed = fxEdit[`${m}|${c}`];
    const shown = typed ?? (v ?? "");
    const bad = typed !== undefined && Number.isNaN(fxParse(typed.trim()));
    return `<td class="num"><input data-fx="${m}" data-cur="${esc(c)}" value="${esc(shown)}" class="${bad ? "invalid" : shown === "" ? "missing" : ""}" inputmode="decimal"></td>`;
  }).join("")}</tr>`).join("") : `<tr><td class="empty">除了本位币还没有别的币种</td></tr>`;
  $("fx-refresh").hidden = !auto.length || !fx.months.includes(fx.this_month);
  $("fx-refresh").disabled = !!fxEdit;
  flash("fx-status", fxMsg);
}
function rateChanges() {
  const out = [];
  for (const [key, typed] of Object.entries(fxEdit)) {
    const [month, cur] = key.split("|"), text = typed.trim(), rate = fxParse(text);
    if (Number.isNaN(rate)) throw new Error(`${fmtMonth(month)}${cur}的汇率要是大于 0 的数字：「${text}」`);
    const old = fx.rates[month]?.[cur] ?? null;
    if (rate === old) continue;
    const after = rate ?? (fxAuto(cur) ? "清空（之后自动取）" : "清空");
    out.push({ text: `${fmtMonth(month)} ${cur}：${old ?? "（空）"} → ${after}${rate != null && fxAuto(cur) ? "（手填，不再自动更新）" : ""}`,
               cell: { month, currency: cur, rate } });
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
  reload() { fxSynced = false; fxMsg = ""; return loadRates(); }, // what was cleared is fetched again
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
