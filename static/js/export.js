// 「导出」: picking transactions and downloading them as CSV.

let exRows = [];
let exChecked = new Map(); // id -> checked, kept while filters change
async function loadExportRows() {
  try {
    exRows = (await api("/api/transactions?" + filterQuery("ex"))).transactions;
  } catch (e) {
    return flash("ex-status", "读取失败：" + e.message);
  }
  if ($("ex-show").value === "new") exRows = exRows.filter(t => !t.export_count);
  for (const t of exRows) if (!exChecked.has(t.id)) exChecked.set(t.id, !t.export_count);
  renderExportRows();
}
function renderExportRows() {
  $("ex-rows").innerHTML = exRows.map(t => `<tr class="${t.export_count ? "exported" : ""}">
    <td><input type="checkbox" data-ex="${t.id}" ${exChecked.get(t.id) ? "checked" : ""}></td>
    <td>${esc(t.date)}</td><td>${esc(t.weekday)}</td><td>${esc(t.account)}</td><td>${esc(t.currency)}</td>
    <td class="num">${fmtAmount(t.amount)}</td><td>${esc(t.category)}</td><td class="wrap">${esc(t.description)}</td>
    <td>${t.export_count ? `${t.export_count} 次，最近 ${esc(fmtTime(t.last_export))}` : ""}</td></tr>`).join("") ||
    `<tr><td colspan="9" class="empty">没有符合条件的交易</td></tr>`;
  const picked = exRows.filter(t => exChecked.get(t.id));
  $("ex-check-all").checked = picked.length > 0 && picked.length === exRows.length;
  $("ex-check-all").indeterminate = picked.length > 0 && picked.length < exRows.length;
  $("ex-go").textContent = `导出 CSV（${picked.length} 条）`;
  $("ex-go").disabled = picked.length === 0;
}
for (const id of ["ex-source", "ex-from", "ex-to", "ex-show"]) $(id).addEventListener("change", loadExportRows);
$("ex-rows").addEventListener("change", e => {
  if (e.target.dataset.ex) { exChecked.set(+e.target.dataset.ex, e.target.checked); renderExportRows(); }
});
$("ex-check-all").onchange = e => { for (const t of exRows) exChecked.set(t.id, e.target.checked); renderExportRows(); };

$("ex-go").onclick = async () => {
  const ids = exRows.filter(t => exChecked.get(t.id)).map(t => t.id);
  const now = new Date();
  const filename = `txreader_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.csv`;
  $("ex-go").disabled = true;
  try {
    const res = await api("/api/exports", send("POST", { ids, filename }));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    flash("ex-status", `已导出 ${ids.length} 条`);
    exChecked = new Map(); // back to the default: tick what hasn't been exported
    loadExportRows();
    loadExportHistory();
  } catch (e) {
    flash("ex-status", "导出失败：" + e.message);
    renderExportRows();
  }
};

async function loadExportHistory() {
  try {
    const { exports } = await api("/api/exports");
    $("ex-history").innerHTML = exports.map(x =>
      `<tr><td>${esc(fmtTime(x.at))}</td><td>${esc(x.filename)}</td><td class="num">${x.count}</td></tr>`).join("") ||
      `<tr><td colspan="3" class="empty">还没有导出过</td></tr>`;
  } catch {}
}
