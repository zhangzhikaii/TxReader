// 「设置」: the tabs, the edit lock, and the account / currency / category lists (rename, delete,
// drag to reorder).

// ---------- edit lock ----------
// Every tab is read-only until 「✎ 修改」. Edits stay on the page until 「保存」, which lists them
// for confirmation and saves them together (all or nothing); 「放弃」 drops them. Dragging to
// reorder is not locked and saves right away.
// Each tab registers { label, editing(), begin(), end(), changes() -> [text] (throws when
// something typed is invalid), save() -> Promise, reload() -> Promise }.
const SET_EDITORS = {};
let setTab = null;
let lockMsg = ""; // shown in the bar until the next action
function lockable(tab, editor) { SET_EDITORS[tab] = editor; }
function pendingEdits(tab) {
  const ed = SET_EDITORS[tab];
  if (!ed?.editing()) return 0;
  try { return ed.changes().length; } catch { return 1; } // something invalid counts as a change
}
function renderLock() {
  const ed = SET_EDITORS[setTab], bar = $("set-lock");
  bar.hidden = !ed;
  if (!ed) return;
  const msg = lockMsg ? `<span class="lock-msg">${esc(lockMsg)}</span>` : "";
  if (!ed.editing()) {
    bar.className = "lockbar";
    bar.innerHTML = `<span class="lock-state">🔒 只读</span>${msg || `<span class="muted">要修改先点「修改」，改完统一保存</span>`}` +
      `<button data-lock="edit">✎ 修改${esc(ed.label)}</button>`;
    return;
  }
  let n = 0, problem = "";
  try { n = ed.changes().length; } catch (e) { problem = e.message; }
  bar.className = "lockbar editing";
  bar.innerHTML = `<span class="lock-state">✎ 正在修改${esc(ed.label)}</span>` +
    (problem ? `<span class="lock-msg warn">${esc(problem)}</span>` : msg || `<span class="muted">${n ? `${n} 处改动，还没保存` : "还没有改动"}</span>`) +
    `<button class="secondary" data-lock="cancel">${n || problem ? "放弃" : "完成"}</button>` +
    `<button data-lock="save" ${n && !problem ? "" : "disabled"}>保存${n ? `（${n} 处）` : ""}</button>`;
}
// Ends editing `tab`; with unsaved edits, asks first. False: the user wants to keep editing.
function leaveEdit(tab) {
  const ed = SET_EDITORS[tab];
  if (!ed?.editing()) return true;
  const n = pendingEdits(tab);
  if (n && !confirm(`「${ed.label}」有 ${n} 处改动还没保存。\n点「确定」放弃这些改动，点「取消」回去接着改。`)) return false;
  ed.end();
  lockMsg = n ? "改动已放弃" : "";
  renderLock();
  return true;
}
async function saveEdits(tab) {
  const ed = SET_EDITORS[tab];
  let list;
  try { list = ed.changes(); } catch (e) { lockMsg = e.message; return renderLock(); }
  if (!list.length) return;
  if (!confirm(`保存这 ${list.length} 处改动？\n\n${list.map(t => "· " + t).join("\n")}`)) return;
  $("set-lock").querySelector('[data-lock="save"]').disabled = true;
  try {
    await ed.save();
  } catch (e) {
    lockMsg = "没有保存（一处也没改）：" + e.message;
    return renderLock();
  }
  ed.end();
  lockMsg = `已保存 ${list.length} 处改动`;
  renderLock();
  await ed.reload();
}
$("set-lock").addEventListener("click", e => {
  const act = e.target.dataset.lock, ed = SET_EDITORS[setTab];
  if (!act || !ed) return;
  lockMsg = "";
  if (act === "edit") { ed.begin(); renderLock(); }
  if (act === "cancel") leaveEdit(setTab);
  if (act === "save") saveEdits(setTab);
});
// Called by route() when another page is opened
const leaveSettings = () => leaveEdit(setTab);
window.addEventListener("beforeunload", e => { if (pendingEdits(setTab)) e.preventDefault(); });

// ---------- account / currency / category lists ----------
const LIST_LABELS = { accounts: "账户", currencies: "币种", categories: "种类" };
let lists = { accounts: [], currencies: [], categories: [], account_types: [] };
const typeLabel = key => lists.account_types.find(t => t.key === (key ?? ""))?.label ?? key;
// While a list is being edited: new names by id, ids to delete, names to add, and for accounts
// the currencies to add / remove and the new type by account id
const listEdit = { accounts: null, currencies: null, categories: null };
async function loadSettings() {
  try { lists = await api("/api/settings"); } catch (e) { return flash("set-status", "读取失败：" + e.message); }
  for (const kind of Object.keys(LIST_LABELS)) renderList(kind);
  loadOpening();
  fxSynced = false; // codes may have changed: rates are fetched again when the tab is shown
  loadRates(); // the rate table has a column per currency, so it follows the currency list
}
function renderList(kind) {
  const items = lists[kind], ed = listEdit[kind];
  const rows = items.map(it => {
    const gone = ed?.deleted.has(it.id), live = ed && !gone;
    let extra = "";
    if (kind === "accounts") {
      const rm = ed?.rmCur[it.id] ?? [], add = ed?.addCur[it.id] ?? [];
      const have = new Set([...it.currencies.map(c => c.name), ...add]);
      const addable = lists.currencies.filter(c => !have.has(c.name));
      const chip = c => {
        const cur = `data-cur="${esc(c.name)}"`;
        if (!live) return `<span class="chip">${esc(c.name)}</span>`;
        if (rm.includes(c.name)) return `<span class="chip removed" title="保存后去掉">${esc(c.name)}<button data-act="undo-rm-cur" ${cur} title="撤销">↺</button></span>`;
        return `<span class="chip">${esc(c.name)}${c.used ? "" : `<button data-act="rm-cur" ${cur} title="去掉这个币种">×</button>`}</span>`;
      };
      extra = `<td>${it.currencies.map(chip).join("")}` +
        add.map(c => `<span class="chip added" title="保存后加上">${esc(c)}<button data-act="undo-add-cur" data-cur="${esc(c)}" title="不加了">×</button></span>`).join("") +
        (live && addable.length ? `<select class="add-cur" data-act="add-cur"><option value="">＋ 币种</option>${addable.map(c => `<option>${esc(c.name)}</option>`).join("")}</select>` : "") + `</td>`;
      const type = ed?.types[it.id] ?? it.type;
      extra = (live ? `<td><select data-act="type" aria-label="账户类型">${lists.account_types.map(t =>
                 `<option value="${t.key}" ${t.key === type ? "selected" : ""}>${esc(t.label)}</option>`).join("")}</select></td>`
               : `<td>${type ? `<span class="tag">${esc(typeLabel(type))}</span>` : `<span class="muted">${esc(typeLabel(""))}</span>`}</td>`) + extra;
    }
    if (kind === "currencies") {
      const code = ed?.codes[it.id] ?? it.code;
      extra = (live ? `<td><input data-act="code" class="code-input" value="${esc(code)}" maxlength="3" placeholder="—" aria-label="币种代码"></td>`
                    : `<td>${it.code ? esc(it.code) : '<span class="muted">—</span>'}</td>`) +
        `<td>${it.is_base ? '<span class="tag done">本位币</span>' : ""}</td>`;
    }
    if (kind === "categories") {
      const fixed = ed?.fixed[it.id] ?? it.fixed;
      extra = `<td>${live ? `<label class="check-cell" title="每月对比默认显示"><input type="checkbox" data-act="fixed" ${fixed ? "checked" : ""}>固定</label>`
                          : fixed ? '<span class="tag done">固定</span>' : ""}</td>`;
    }
    const name = live ? `<input data-act="name" value="${esc(ed.names[it.id] ?? it.name)}">` : esc(it.name);
    const del = !ed ? "" : gone ? `<button class="icon" data-act="undelete" title="撤销删除">↺</button>`
      : it.blockers.length ? `<span class="muted" title="${esc("不能删除：" + it.blockers.join("；"))}">🔒</span>`
      : `<button class="icon" data-act="delete" title="删除（没有任何地方用到它）">✕</button>`;
    return `<tr data-kind="${kind}" data-id="${it.id}" class="${gone ? "deleted" : ""}">
      <td>${items.length > 1 ? `<span class="handle" title="按住拖动调整顺序" aria-label="拖动调整顺序">☰</span>` : ""}</td>
      <td>${name}</td>${extra}<td class="num">${it.used}</td><td>${del}</td></tr>`;
  });
  const added = (ed?.added ?? []).map((n, i) => `<tr class="added"><td></td><td>${esc(n)} <span class="tag">新增</span></td>` +
    (kind === "accounts" ? `<td class="muted">保存后再设</td><td class="muted">保存后再加币种</td>` : kind === "categories" ? `<td class="muted">保存后再设</td>` : `<td class="muted">保存后再设</td><td></td>`) +
    `<td class="num">0</td><td><button class="icon" data-act="unadd" data-i="${i}" title="不添加了">✕</button></td></tr>`);
  $("set-" + kind).innerHTML = [...rows, ...added].join("") || `<tr><td colspan="6" class="empty">还没有${LIST_LABELS[kind]}</td></tr>`;
  $(`set-${kind}-new`).closest(".add-row").hidden = !ed;
}
// The edits of one list, in the order they are saved: { text (for the confirmation), op }
function listChanges(kind) {
  const ed = listEdit[kind], label = LIST_LABELS[kind], items = lists[kind], out = [];
  for (const it of items) if (ed.deleted.has(it.id))
    out.push({ text: `删除${label}「${it.name}」`, op: { op: "delete", kind, id: it.id } });
  for (const it of items) {
    if (ed.deleted.has(it.id) || !(it.id in ed.names)) continue;
    const name = ed.names[it.id].trim();
    if (!name) throw new Error(`${label}「${it.name}」的新名称是空的`);
    if (name !== it.name) out.push({ text: `${label}「${it.name}」改名为「${name}」${it.used ? `（已有 ${it.used} 笔交易一起改）` : ""}`,
                                     op: { op: "rename", kind, id: it.id, name } });
  }
  if (kind === "categories") for (const it of items) {
    if (ed.deleted.has(it.id) || !(it.id in ed.fixed) || ed.fixed[it.id] === it.fixed) continue;
    out.push({ text: `种类「${it.name}」${ed.fixed[it.id] ? "设为固定" : "不再是固定"}`, op: { op: "set_fixed", kind, id: it.id, fixed: ed.fixed[it.id] } });
  }
  if (kind === "accounts") for (const it of items) {
    if (ed.deleted.has(it.id) || !(it.id in ed.types) || ed.types[it.id] === it.type) continue;
    out.push({ text: `账户「${it.name}」类型从「${typeLabel(it.type)}」改为「${typeLabel(ed.types[it.id])}」`,
               op: { op: "set_type", kind, id: it.id, type: ed.types[it.id] } });
  }
  if (kind === "currencies") for (const it of items) {
    if (ed.deleted.has(it.id) || !(it.id in ed.codes)) continue;
    const code = ed.codes[it.id].trim().toUpperCase();
    if (code && !/^[A-Z]{3}$/.test(code)) throw new Error(`币种「${it.name}」的代码要是三个字母（比如 JPY）：「${code}」`);
    if (code === it.code) continue;
    out.push({ text: `币种「${it.name}」的代码${it.code ? `从 ${it.code} ` : ""}改为${code || "空（汇率手填）"}` +
               (it.code ? `（自动取的${it.is_base ? "所有" : "这个币种的"}汇率会重新取）` : ""),
               op: { op: "set_code", kind, id: it.id, code } });
  }
  for (const name of ed.added) out.push({ text: `新增${label}「${name}」`, op: { op: "add", kind, name } });
  if (kind === "accounts") for (const it of items) {
    if (ed.deleted.has(it.id)) continue;
    for (const c of ed.addCur[it.id] ?? []) out.push({ text: `「${it.name}」加上币种${c}`, op: { op: "add_currency", id: it.id, name: c } });
    for (const c of ed.rmCur[it.id] ?? []) out.push({ text: `「${it.name}」去掉币种${c}`, op: { op: "remove_currency", id: it.id, name: c } });
  }
  return out;
}
for (const kind of Object.keys(LIST_LABELS)) lockable(kind, {
  label: LIST_LABELS[kind],
  editing: () => !!listEdit[kind],
  begin() { listEdit[kind] = { names: {}, deleted: new Set(), added: [], addCur: {}, rmCur: {}, fixed: {}, types: {}, codes: {} }; renderList(kind); },
  end() { listEdit[kind] = null; renderList(kind); },
  changes: () => listChanges(kind).map(c => c.text),
  save: () => api("/api/settings/batch", send("POST", { ops: listChanges(kind).map(c => c.op) })),
  async reload() { await loadSettings(); loadSuggestions(); }, // manual entry offers the new lists
});

function stageAdd(kind) {
  const input = $(`set-${kind}-new`), ed = listEdit[kind], name = input.value.trim();
  if (!ed) return;
  if (!name) return input.focus();
  if (lists[kind].some(it => it.name === name && !ed.deleted.has(it.id)) || ed.added.includes(name)) {
    lockMsg = `已经有${LIST_LABELS[kind]}「${name}」了`;
    return renderLock();
  }
  ed.added.push(name);
  input.value = "";
  input.focus();
  lockMsg = "";
  renderList(kind);
  renderLock();
}
const findItem = tr => lists[tr.dataset.kind].find(x => x.id === +tr.dataset.id);
$("view-settings").addEventListener("click", e => {
  const add = e.target.dataset.add;
  if (add) return stageAdd(add);
  const act = e.target.dataset.act, tr = e.target.closest("tr");
  const kind = tr?.closest("tbody")?.id.replace(/^set-/, ""), ed = listEdit[kind];
  if (!act || !ed) return;
  const it = tr.dataset.kind ? findItem(tr) : null, cur = e.target.dataset.cur;
  const drop = (arr, v) => arr.filter(x => x !== v);
  if (act === "delete") ed.deleted.add(it.id);
  else if (act === "undelete") ed.deleted.delete(it.id);
  else if (act === "unadd") ed.added.splice(+e.target.dataset.i, 1);
  else if (act === "rm-cur") (ed.rmCur[it.id] ??= []).push(cur);
  else if (act === "undo-rm-cur") ed.rmCur[it.id] = drop(ed.rmCur[it.id], cur);
  else if (act === "undo-add-cur") ed.addCur[it.id] = drop(ed.addCur[it.id], cur);
  else return;
  lockMsg = "";
  renderList(kind);
  renderLock();
});
$("view-settings").addEventListener("input", e => {
  const tr = e.target.closest("tr[data-kind]");
  if (!["name", "code"].includes(e.target.dataset.act) || !tr || !listEdit[tr.dataset.kind]) return;
  listEdit[tr.dataset.kind][e.target.dataset.act === "code" ? "codes" : "names"][tr.dataset.id] = e.target.value;
  lockMsg = "";
  renderLock();
});
$("view-settings").addEventListener("change", e => {
  const tr = e.target.closest("tr[data-kind]");
  if (e.target.dataset.act === "fixed" && tr && listEdit.categories) {
    listEdit.categories.fixed[tr.dataset.id] = e.target.checked;
    lockMsg = "";
    return renderLock();
  }
  if (e.target.dataset.act === "type" && tr && listEdit.accounts) {
    listEdit.accounts.types[tr.dataset.id] = e.target.value;
    lockMsg = "";
    return renderLock();
  }
  if (e.target.dataset.act !== "add-cur" || !e.target.value || !tr) return;
  const ed = listEdit.accounts;
  if (!ed) return;
  (ed.addCur[tr.dataset.id] ??= []).push(e.target.value);
  lockMsg = "";
  renderList("accounts");
  renderLock();
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
  // Put the row before the first other row whose middle is below the pointer (rows being
  // added stay at the end)
  const others = [...drag.tbody.querySelectorAll("tr[data-kind]")].filter(r => r !== drag.tr);
  const next = others.find(r => { const b = r.getBoundingClientRect(); return e.clientY < b.top + b.height / 2; });
  const at = next ?? drag.tbody.querySelector("tr.added");
  if (at !== drag.tr.nextElementSibling) drag.tbody.insertBefore(drag.tr, at);
});
async function endDrag() {
  if (!drag) return;
  const { tr, tbody, kind, before } = drag;
  drag = null;
  tr.classList.remove("dragging");
  document.body.classList.remove("reordering");
  const ids = rowIds(tbody);
  if (ids.join() === before) return;
  try {
    await api(`/api/settings/${kind}/order`, send("POST", { ids }));
    flash("set-status", "顺序已保存");
  } catch (e) {
    flash("set-status", "顺序没有保存：" + e.message);
  }
  await loadSettings(); // edits in progress are kept: they are rendered again from listEdit
  loadSuggestions();
}
// Categories by number of transactions, most first; ties keep their order. Not locked, like dragging.
$("set-categories-by-use").onclick = async () => {
  const items = lists.categories;
  const ids = items.map((it, i) => [it, i]).sort((a, b) => (b[0].used - a[0].used) || (a[1] - b[1])).map(x => x[0].id);
  if (ids.join() === items.map(it => it.id).join()) return flash("set-status", "已经是按交易数排列的了");
  if (!confirm("把种类按交易笔数从多到少重新排列？\n（笔数一样的保持原来的先后；之后还可以拖动调整）")) return;
  try {
    await api("/api/settings/categories/order", send("POST", { ids }));
    flash("set-status", "已按交易数排序");
  } catch (e) {
    return flash("set-status", "顺序没有保存：" + e.message);
  }
  await loadSettings(); // edits in progress are kept
  loadSuggestions();
};
$("view-settings").addEventListener("pointerup", endDrag);
$("view-settings").addEventListener("pointercancel", endDrag);

$("view-settings").addEventListener("keydown", e => {
  if (e.key !== "Enter" || e.isComposing) return;
  const kind = e.target.id?.match(/^set-(\w+)-new$/)?.[1];
  if (kind) stageAdd(kind);
  else if (["name", "code"].includes(e.target.dataset.act)) e.target.blur();
});

// ---------- tabs: one at a time, remembered ----------
const SET_TABS = ["accounts", "currencies", "categories", "opening", "rates", "email"];
function showSetTab(tab) {
  if (!SET_TABS.includes(tab)) tab = "accounts";
  if (tab !== setTab && !leaveEdit(setTab)) return; // unsaved edits and the user stays
  setTab = tab;
  lockMsg = "";
  save("tx.settings.tab", tab);
  for (const el of document.querySelectorAll("[data-set-panel]")) el.hidden = !el.dataset.setPanel.split(" ").includes(tab);
  for (const b of document.querySelectorAll("#set-tabs button")) b.classList.toggle("active", b.dataset.setTab === tab);
  renderLock();
  if (tab === "rates") fxAutoSync();
}
$("set-tabs").addEventListener("click", e => { const b = e.target.closest("[data-set-tab]"); if (b) showSetTab(b.dataset.setTab); });
