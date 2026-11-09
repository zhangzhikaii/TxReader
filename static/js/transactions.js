// 「交易」: kept transactions, filters, editing category and description.

$("tx-source").innerHTML = `<option value="">全部</option><option value="manual">手动输入</option><option value="email">邮件导入</option>` +
  `<optgroup label="截图识别">` +
  Object.entries(SOURCE_LABELS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("") + `</optgroup>`;
function filterQuery(p) {
  const q = new URLSearchParams();
  const fields = { source: "source", date_from: "from", date_to: "to", account: "account", currency: "currency", category: "category" };
  for (const [param, id] of Object.entries(fields)) {
    const el = $(`${p}-${id}`);
    if (el?.value) q.set(param, el.value);
  }
  return q.toString();
}
// The transactions page opens on the current month; after that the dates are what was chosen
{
  const now = new Date();
  $("tx-from").value = ymd(new Date(now.getFullYear(), now.getMonth(), 1));
  $("tx-to").value = ymd(new Date(now.getFullYear(), now.getMonth() + 1, 0));
}
// Account / currency / category filters offer the settings lists, keeping what is chosen
function fillTxFilters() {
  const fill = (id, label, names) => {
    const el = $(id), keep = el.value;
    el.innerHTML = `<option value="">全部${label}</option>` + names.map(n => `<option>${esc(n)}</option>`).join("");
    el.value = names.includes(keep) ? keep : "";
  };
  fill("tx-account", "账户", sugg.accounts.map(a => a.name));
  fill("tx-currency", "币种", sugg.currencies);
  fill("tx-category", "种类", sugg.categories);
}

let txRows = [];
async function loadTransactions() {
  const query = filterQuery("tx");
  try {
    txRows = (await api("/api/transactions?" + query)).transactions;
  } catch (e) {
    return flash("tx-status", "读取失败：" + e.message);
  }
  $("tx-rows").innerHTML = txRows.map(t => {
    const shot = t.image_id != null, mail = t.email_id != null; // manual rows have nothing to show
    return `<tr class="${shot || mail ? "clickable" : ""}" ${shot ? `data-img-id="${t.image_id}" data-name="${esc(t.image_name)}" title="点击查看原截图"` : ""}` +
      ` ${mail ? `data-mail-id="${t.email_id}" title="点击查看原邮件"` : ""}>
    <td>${esc(t.date)}</td><td>${esc(t.weekday)}</td><td>${esc(t.account)}</td><td>${esc(t.currency)}</td>
    <td class="num">${fmtAmount(t.amount)}</td>
    <td><input data-tx="${t.id}" data-k="category" value="${esc(t.category)}" list="m-categories"></td>
    <td><input data-tx="${t.id}" data-k="description" value="${esc(t.description)}"></td></tr>`;
  }).join("");
  flash("tx-status", txRows.length ? `共 ${txRows.length} 条` : "没有符合条件的交易");
  $("tx-export").disabled = !txRows.length;
  txQuery = query;
}
// Export what is listed: the same filters, asked of the server again
let txQuery = "";
$("tx-export").onclick = async () => {
  const now = new Date();
  const filename = `txreader_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.csv`;
  $("tx-export").disabled = true;
  try {
    const res = await api("/api/transactions/export?" + txQuery);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    flash("tx-status", `已导出 ${txRows.length} 条`);
  } catch (e) {
    flash("tx-status", "导出失败：" + e.message);
  }
  $("tx-export").disabled = !txRows.length;
};
for (const id of ["tx-source", "tx-from", "tx-to", "tx-account", "tx-currency", "tx-category"]) $(id).addEventListener("change", loadTransactions);
$("tx-rows").addEventListener("change", async e => {
  const { tx: id, k } = e.target.dataset;
  if (!id) return;
  const value = e.target.value;
  const t = txRows.find(t => t.id === +id);
  if (k === "category" && !value.trim()) {
    e.target.value = t?.category ?? "";
    return flash("tx-status", "种类不能为空");
  }
  const save = body => api(`/api/transactions/${id}`, send("PATCH", body));
  try {
    if (k === "category") {
      // Changing a stored transaction's category is confirmed; a new category is added to the list
      const name = value.trim(), old = t?.category ?? "";
      if (name === old) return;
      const isNew = !knownCategories.has(name);
      const what = t ? `${t.date.slice(0, 10)}　${fmtAmount(t.amount)} ${t.currency}${t.description ? "　" + t.description : ""}` : "";
      if (!confirm(`把这笔交易的种类从「${old}」改为「${name}」？\n${what}` +
                   (isNew ? `\n\n「${name}」还不在种类清单里，会一起新增。` : ""))) {
        e.target.value = old;
        return flash("tx-status", "没有修改");
      }
      await save({ category: name, add_category: isNew });
      if (t) t.category = name;
      e.target.value = name;
      if (isNew) { await loadSuggestions(); fillTxFilters(); }
      return flash("tx-status", isNew ? `已新增种类「${name}」，这笔交易已改为「${name}」` : `种类已改为「${name}」`);
    }
    await save({ [k]: value });
    if (t) t[k] = value;
    flash("tx-status", "描述已保存");
  } catch (err) {
    if (k === "category") e.target.value = t?.category ?? "";
    flash("tx-status", "保存失败：" + err.message);
  }
});
$("tx-rows").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.isComposing && e.target.dataset.tx) e.target.blur();
});
$("tx-rows").addEventListener("click", e => {
  if (e.target.closest("input")) return;
  const tr = e.target.closest("tr[data-img-id]");
  if (tr) return toggleServerImage(+tr.dataset.imgId, tr.dataset.name);
  const mailTr = e.target.closest("tr[data-mail-id]");
  if (mailTr) toggleEmail(+mailTr.dataset.mailId);
});
