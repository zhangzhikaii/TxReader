// 「设置」: the tabs, and the account / currency / category lists (rename, delete, drag to reorder).

const LIST_LABELS = { accounts: "账户", currencies: "币种", categories: "种类" };
let lists = { accounts: [], currencies: [], categories: [] };
async function loadSettings() {
  try { lists = await api("/api/settings"); } catch (e) { return flash("set-status", "读取失败：" + e.message); }
  for (const kind of Object.keys(LIST_LABELS)) renderList(kind);
  loadOpening();
}
function renderList(kind) {
  const items = lists[kind];
  $("set-" + kind).innerHTML = items.map(it => {
    let extra = "";
    if (kind === "accounts") {
      const have = new Set(it.currencies.map(c => c.name));
      const addable = lists.currencies.filter(c => !have.has(c.name));
      extra = `<td>${it.currencies.map(c =>
        `<span class="chip">${esc(c.name)}<button data-act="rm-cur" data-cur="${esc(c.name)}" title="${c.used ? `已有 ${c.used} 笔交易，不能去掉` : "去掉这个币种"}" ${c.used ? "disabled" : ""}>×</button></span>`).join("")}` +
        (addable.length ? `<select class="add-cur" data-act="add-cur"><option value="">＋ 币种</option>${addable.map(c => `<option>${esc(c.name)}</option>`).join("")}</select>` : "") + `</td>`;
    }
    if (kind === "currencies") {
      extra = `<td>${it.is_base ? '<span class="tag done">本位币</span>' : ""}</td>`;
    }
    return `<tr data-kind="${kind}" data-id="${it.id}">
      <td>${items.length > 1 ? `<span class="handle" title="按住拖动调整顺序" aria-label="拖动调整顺序">☰</span>` : ""}</td>
      <td><input data-act="name" value="${esc(it.name)}"></td>${extra}
      <td class="num">${it.used}</td>
      <td>${it.used ? "" : `<button class="icon" data-act="delete" title="删除（还没有交易用它）">✕</button>`}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">还没有${LIST_LABELS[kind]}</td></tr>`;
}
async function listCall(path, opts, done) {
  try {
    await api(path, opts);
    flash("set-status", done);
  } catch (e) {
    flash("set-status", "失败：" + e.message);
  }
  await loadSettings();
  loadSuggestions(); // manual entry offers the new lists
}
const findItem = tr => lists[tr.dataset.kind].find(x => x.id === +tr.dataset.id);
$("view-settings").addEventListener("click", e => {
  const add = e.target.dataset.add;
  if (add) {
    const input = $(`set-${add}-new`);
    if (!input.value.trim()) return input.focus();
    listCall(`/api/settings/${add}`, send("POST", { name: input.value }), `已添加${LIST_LABELS[add]}「${input.value.trim()}」`)
      .then(() => { input.value = ""; input.focus(); });
    return;
  }
  const act = e.target.dataset.act, tr = e.target.closest("tr[data-kind]");
  if (!act || !tr) return;
  const { kind } = tr.dataset, it = findItem(tr);
  if (act === "delete") {
    if (confirm(`删除${LIST_LABELS[kind]}「${it.name}」？`))
      listCall(`/api/settings/${kind}/${it.id}`, { method: "DELETE" }, `已删除「${it.name}」`);
  } else if (act === "rm-cur") {
    listCall(`/api/settings/accounts/${it.id}/currencies/${encodeURIComponent(e.target.dataset.cur)}`, { method: "DELETE" },
      `「${it.name}」已去掉${e.target.dataset.cur}`);
  }
});
$("view-settings").addEventListener("change", e => {
  const act = e.target.dataset.act, tr = e.target.closest("tr[data-kind]");
  if (!act || !tr) return;
  const { kind } = tr.dataset, it = findItem(tr);
  const patch = (body, done) => listCall(`/api/settings/${kind}/${it.id}`, send("PATCH", body), done);
  if (act === "name") {
    const name = e.target.value.trim();
    if (!name || name === it.name) { e.target.value = it.name; return; }
    if (it.used && !confirm(`把${LIST_LABELS[kind]}「${it.name}」改名为「${name}」？\n已有 ${it.used} 笔交易会一起改。`)) {
      e.target.value = it.name;
      return;
    }
    patch({ name }, `已改名为「${name}」`);
  } else if (act === "add-cur" && e.target.value) {
    listCall(`/api/settings/accounts/${it.id}/currencies`, send("POST", { name: e.target.value }),
      `「${it.name}」已加上${e.target.value}`);
  }
});
// Drag the ☰ handle to reorder: the row follows the pointer; the new order is saved on release
let drag = null;
const rowIds = tbody => [...tbody.querySelectorAll("tr[data-kind]")].map(r => +r.dataset.id);
$("view-settings").addEventListener("pointerdown", e => {
  const handle = e.target.closest(".handle");
  if (!handle || e.button > 0) return;
  e.preventDefault();
  handle.setPointerCapture(e.pointerId); // keep getting moves even outside the handle
  const tr = handle.closest("tr");
  drag = { tr, tbody: tr.parentElement, kind: tr.dataset.kind, before: rowIds(tr.parentElement).join() };
  tr.classList.add("dragging");
  document.body.classList.add("reordering");
});
$("view-settings").addEventListener("pointermove", e => {
  if (!drag) return;
  // Put the row before the first other row whose middle is below the pointer
  const others = [...drag.tbody.querySelectorAll("tr[data-kind]")].filter(r => r !== drag.tr);
  const next = others.find(r => { const b = r.getBoundingClientRect(); return e.clientY < b.top + b.height / 2; });
  if (next !== drag.tr.nextElementSibling) drag.tbody.insertBefore(drag.tr, next ?? null);
});
function endDrag() {
  if (!drag) return;
  const { tr, tbody, kind, before } = drag;
  drag = null;
  tr.classList.remove("dragging");
  document.body.classList.remove("reordering");
  const ids = rowIds(tbody);
  if (ids.join() !== before) listCall(`/api/settings/${kind}/order`, send("POST", { ids }), "顺序已保存");
}
$("view-settings").addEventListener("pointerup", endDrag);
$("view-settings").addEventListener("pointercancel", endDrag);

$("view-settings").addEventListener("keydown", e => {
  if (e.key !== "Enter" || e.isComposing) return;
  const kind = e.target.id?.match(/^set-(\w+)-new$/)?.[1];
  if (kind) document.querySelector(`[data-add="${kind}"]`).click();
  else if (e.target.dataset.act === "name") e.target.blur();
});

// One tab at a time, remembered
const SET_TABS = ["accounts", "currencies", "categories", "opening", "rates", "email"];
function showSetTab(tab) {
  if (!SET_TABS.includes(tab)) tab = "accounts";
  save("tx.settings.tab", tab);
  for (const el of document.querySelectorAll("[data-set-panel]")) el.hidden = !el.dataset.setPanel.split(" ").includes(tab);
  for (const b of document.querySelectorAll("#set-tabs button")) b.classList.toggle("active", b.dataset.setTab === tab);
}
$("set-tabs").addEventListener("click", e => { const b = e.target.closest("[data-set-tab]"); if (b) showSetTab(b.dataset.setTab); });
showSetTab(load("tx.settings.tab"));
