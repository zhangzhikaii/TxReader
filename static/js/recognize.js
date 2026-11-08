// 「记一笔 › 截图识别」: sources and their defaults, uploading, and the pending rows to review
// (shared with email import, see pendingSource).

const SOURCES = {
  wechat: { hint: "点击或拖拽微信「零钱明细」列表截图到这里（可多张）",
            defaults: { account: "微信", currency: "人民币", category: "饮食费" } },
  boc:    { hint: "点击或拖拽中国银行单笔交易的「明细」页截图到这里（每张一笔，可多张）",
            defaults: { account: "中行", currency: "人民币", category: "饮食费" } },
  icbc:   { hint: "点击或拖拽工商银行「收支明细」列表截图到这里（可多张）",
            defaults: { account: "工行", currency: "人民币", category: "饮食费" } },
  suica:  { hint: "点击或拖拽 Apple 钱包里 Suica 单笔交易的详情页截图到这里（每张一笔，可多张）",
            defaults: { account: "Suica", currency: "日元", category: "交通费" } },
  receipt: { hint: "点击或拖拽小票照片到这里（每张一笔，可多张）。店名是尽力识别的，请对照大图核对",
            defaults: { account: "现金", currency: "日元", category: "饮食费" } },
};
const SOURCE_LABELS = Object.fromEntries([...$("source").options].map(o => [o.value, o.textContent]));
const GLOBAL_SETTINGS = ["source", "year", "order"];
const SOURCE_SETTINGS = ["account", "currency", "category"]; // remembered separately per source
let records = [];   // pending rows of the current source, as loaded from the server
let skipped = [];   // rows of this session's uploads that were already in the books
let images = [];    // screenshots uploaded in this session: { key, name, url }
let nextImgId = 1;
let viewing = null; // key of the screenshot shown in the viewer
let session = 0;    // bumped on source switch, so results of uploads still in flight are dropped
let recordsSource = null; // the source `records` belong to: a screenshot source key or "email"
// The pending rows shown: email rows on the email tab, else the chosen screenshot source's
const pendingSource = () => method === "email" ? "email" : $("source").value;
const pflash = msg => flash(method === "email" ? "e-status" : "status", msg);

for (const k of GLOBAL_SETTINGS) {
  const v = load("tx." + k);
  if (v) $(k).value = v;
  $(k).addEventListener("change", () => save("tx." + k, $(k).value));
}
if (!$("year").value) $("year").value = new Date().getFullYear();

const defaultsFor = src => Object.fromEntries(SOURCE_SETTINGS.map(k =>
  // "tx.<k>" is the pre-multi-source key, kept so existing WeChat settings carry over
  [k, load(`tx.${src}.${k}`) ?? (src === "wechat" ? load("tx." + k) : null) ?? SOURCES[src].defaults[k]]));

function applySource() {
  const src = $("source").value;
  const d = defaultsFor(src);
  for (const k of SOURCE_SETTINGS) $(k).value = d[k];
  $("drop").textContent = SOURCES[src].hint;
}
for (const k of SOURCE_SETTINGS) {
  $(k).addEventListener("change", () => {
    save(`tx.${$("source").value}.${k}`, $(k).value);
    // Rows not edited by hand follow the default
    for (const r of records) if (!r.own.has(k)) r[k] = $(k).value;
    renderPending();
  });
}
// Rows and screenshots of one source don't belong with another's, so start over on switch
$("source").addEventListener("change", () => { clearSession(); applySource(); loadPending(); });
applySource();

function updateCounts(counts) {
  for (const o of $("source").options) {
    const n = counts[o.value] || 0;
    o.textContent = SOURCE_LABELS[o.value] + (n ? `（${n} 条待核对）` : "");
  }
  const n = counts.email || 0;
  document.querySelector('[data-method="email"]').innerHTML = "邮件导入" + (n ? `<span class="soon">${n} 条待核对</span>` : "");
}

function sortRecords() {
  const dir = $("order").value === "desc" ? -1 : 1;
  records.sort((a, b) => dir * a.date.localeCompare(b.date));
}
$("order").addEventListener("change", () => { sortRecords(); renderPending(); });

// ---------- recognize: pending rows ----------

// Replace the table with the server's pending list, keeping checkboxes the user already set
function setPending(rows) {
  const prev = new Map(records.map(r => [r.id, r.checked]));
  // Email rows bring their own account / currency / category (from the rule)
  const d = pendingSource() === "email" ? {}
    : { account: $("account").value, currency: $("currency").value, category: $("category").value };
  records = rows.map(r => {
    const own = new Set(SOURCE_SETTINGS.filter(k => k in r)); // values edited by hand earlier
    return { ...d, ...r, own, checked: prev.get(r.id) ?? !r.suspect };
  });
  sortRecords();
  renderPending();
}

async function loadPending() {
  const src = pendingSource();
  if (recordsSource !== src) { clearSession(); recordsSource = src; } // switched between screenshots and email
  const mySession = session;
  try {
    const data = await api(`/api/pending?source=${src}`);
    if (mySession !== session) return;
    updateCounts(data.counts);
    setPending(data.pending);
  } catch (e) {
    pflash("读取待核对失败：" + e.message);
  }
}

const drop = $("drop");
drop.onclick = () => $("file").click();
drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); };
$("file").onchange = e => { upload(e.target.files); e.target.value = ""; };

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) {
    fd.append("files", f);
    images.push({ key: "local-" + nextImgId++, name: f.name, url: URL.createObjectURL(f) });
  }
  renderThumbs();
  fd.append("year", $("year").value);
  fd.append("source", $("source").value);
  flash("status", `识别中（${files.length} 张）…`);
  const mySession = session;
  try {
    const data = await api("/api/parse", { method: "POST", body: fd });
    if (mySession !== session) return; // source switched while recognizing
    const c = data.counts;
    skipped = skipped.concat(data.skipped);
    setPending(data.pending);
    flash("status", `识别到 ${c.recognized} 条：新增 ${c.new} 条` +
      (c.suspect ? `，疑似重复 ${c.suspect} 条` : "") +
      (c.merge ? `，${c.merge} 条已在待核对里` : "") +
      (c.skip ? `，${c.skip} 条已入库（跳过）` : "") +
      (data.errors.length ? `；失败：${data.errors.join("；")}` : ""));
    loadCounts();
  } catch (e) {
    if (mySession !== session) return;
    flash("status", "出错了：" + e.message);
  }
}
async function loadCounts() {
  try { updateCounts((await api(`/api/pending?source=${pendingSource()}`)).counts); } catch {}
}

const byId = id => records.find(r => r.id === +id);

function suspectTip(s) {
  return `疑似重复：与${s.confirmed ? "已入库" : "待核对"}的「${s.date} ${fmtAmount(s.amount)} ${s.merchant}」` +
    "日期和金额相同、商户不同。确认是重复就删除这一行";
}

function renderPending() {
  // Remember which cell had focus so it can be restored after rebuilding the table
  const active = document.activeElement?.dataset ?? {};
  $("result").hidden = method === "manual" || (records.length === 0 && skipped.length === 0);
  $("no-pending").hidden = records.length > 0;
  $("rows").innerHTML = records.map((r, i) => {
    const warn = r.year_guessed || r.sign_guessed;
    const tip = [r.year_guessed && "日期为推测", r.sign_guessed && "正负号为推测"].filter(Boolean).join("，");
    const newDay = i > 0 && r.date.slice(0, 10) !== records[i - 1].date.slice(0, 10);
    const imgKey = "srv-" + r.image_id;
    return `<tr class="${r.amount > 0 ? "income" : ""} ${r.suspect ? "suspect" : ""} ${warn ? "warn" : ""} ${newDay ? "newday" : ""}">
      <td><input type="checkbox" data-check="${r.id}" ${r.checked ? "checked" : ""}></td>
      <td class="date"><input data-id="${r.id}" data-k="date" value="${esc(r.date)}" class="${r.year_guessed ? "guess" : ""}" title="${r.year_guessed ? "日期为推测，请核对" : ""}"></td>
      <td>${esc(r.weekday)}</td>
      <td><input data-id="${r.id}" data-k="account" value="${esc(r.account)}" list="m-accounts"></td>
      <td><input data-id="${r.id}" data-k="currency" value="${esc(r.currency)}"></td>
      <td class="amt"><input data-id="${r.id}" data-k="amount" value="${fmtAmount(r.amount)}"></td>
      <td><input data-id="${r.id}" data-k="category" value="${esc(r.category)}" list="m-categories"></td>
      <td><input data-id="${r.id}" data-k="merchant" value="${esc(r.merchant)}"></td>
      <td class="flag" title="${esc(r.suspect ? suspectTip(r.suspect) : tip)}">${warn ? "⚠" : ""}${r.suspect ? ' <span class="tag">疑似重复</span>' : ""}</td>
      <td>${r.email_id != null
        ? `<button class="icon ${viewing === "mail-" + r.email_id ? "active" : ""}" data-view-mail="${r.email_id}" data-name="${esc(r.email_subject)}" title="查看原邮件" tabindex="-1">✉</button>`
        : `<button class="icon ${viewing === imgKey ? "active" : ""}" data-view-img="${r.image_id}" data-name="${esc(r.image_name)}" title="查看原截图" tabindex="-1">🖼</button>`}</td>
      <td><button class="secondary" data-del="${r.id}" tabindex="-1">删除</button></td></tr>`;
  }).join("");
  const n = records.filter(r => r.checked).length;
  $("check-all").checked = n > 0 && n === records.length;
  $("check-all").indeterminate = n > 0 && n < records.length;
  $("confirm").textContent = `提交入库（${n} 条）`;
  $("confirm").disabled = $("delete-checked").disabled = n === 0;
  $("skipped").hidden = skipped.length === 0;
  $("skipped").querySelector("summary").textContent = `已入库，跳过 ${skipped.length} 条`;
  $("skipped-rows").innerHTML = skipped.map(s =>
    `<tr><td>${esc(s.date)}</td><td class="num">${fmtAmount(s.amount)}</td><td>${esc(s.merchant)}</td></tr>`).join("");
  if (active.id && active.k) $("rows").querySelector(`input[data-id="${active.id}"][data-k="${active.k}"]`)?.focus();
}

// Edits are saved to the server as a draft, so they survive a reload
async function saveDraft(r, fields) {
  try {
    await api(`/api/pending/${r.id}`, send("PATCH", fields));
  } catch (e) {
    pflash("保存修改失败：" + e.message);
  }
}

$("rows").addEventListener("input", e => {
  const { id, k } = e.target.dataset;
  if (!id || !k || k === "date") return; // dates are applied on change, once complete
  byId(id)[k] = k === "amount" ? parseAmount(e.target.value) : e.target.value;
});
$("rows").addEventListener("change", e => {
  const check = e.target.dataset.check;
  if (check) { byId(check).checked = e.target.checked; renderPending(); return; }
  const { id, k } = e.target.dataset;
  const r = id && byId(id);
  if (!r || !k) return;
  if (k === "amount") {
    // Update in place (a full re-render isn't needed)
    e.target.value = fmtAmount(r.amount);
    e.target.closest("tr").classList.toggle("income", r.amount > 0);
    saveDraft(r, { amount: r.amount });
  } else if (k === "date") {
    const parsed = parseDate(e.target.value);
    if (!parsed) {
      e.target.classList.add("invalid");
      pflash(`日期格式不对：「${e.target.value}」，应为 2026/10/29 22:12:00`);
      return;
    }
    Object.assign(r, parsed, { year_guessed: false }); // user has checked this date
    pflash("");
    saveDraft(r, { date: r.date });
    sortRecords();
    renderPending();
  } else {
    r.own.add(k);
    saveDraft(r, { [k]: r[k] });
  }
});
// Enter: same column, next row (Shift+Enter: previous row). Tab moves right natively.
// On the last row Enter still applies the edit
tableKeys($("rows"), el => el.dataset.k ? `input[data-k="${el.dataset.k}"]` : null,
  el => el.dispatchEvent(new Event("change", { bubbles: true })));
$("rows").addEventListener("click", e => {
  const img = e.target.closest("[data-view-img]");
  if (img) return toggleServerImage(+img.dataset.viewImg, img.dataset.name);
  const mail = e.target.closest("[data-view-mail]");
  if (mail) return toggleEmail(+mail.dataset.viewMail, mail.dataset.name);
  const id = e.target.dataset.del;
  if (id !== undefined) deletePending([+id]);
});
$("check-all").onchange = e => { for (const r of records) r.checked = e.target.checked; renderPending(); };

async function deletePending(ids) {
  try {
    await api("/api/pending/delete", send("POST", { ids }));
    records = records.filter(r => !ids.includes(r.id));
    renderPending();
    loadCounts();
  } catch (e) {
    pflash("删除失败：" + e.message);
  }
}
$("delete-checked").onclick = () => {
  const ids = records.filter(r => r.checked).map(r => r.id);
  if (ids.length && confirm(`删除勾选的 ${ids.length} 条待核对记录？`)) deletePending(ids);
};

$("confirm").onclick = async () => {
  const rows = records.filter(r => r.checked);
  if ($("rows").querySelector("input.invalid")) {
    if (!confirm("有日期格式不对的行，这些行会按原来的日期提交。继续吗？")) return;
  }
  $("confirm").disabled = true;
  try {
    const { confirmed } = await api("/api/confirm", send("POST", { rows: rows.map(r => ({
      id: r.id, date: r.date, account: r.account, currency: r.currency, amount: r.amount,
      category: r.category, description: r.merchant,
    })) }));
    pflash(`已入库 ${confirmed} 条`);
    await loadPending();
  } catch (e) {
    pflash("提交失败：" + e.message);
    renderPending();
  }
};

function clearSession() {
  session++;
  records = [];
  skipped = [];
  for (const im of images) URL.revokeObjectURL(im.url);
  images = [];
  showImage(null);
  flash("status", "");
  flash("e-status", "");
  renderPending();
}
