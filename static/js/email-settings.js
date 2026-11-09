// 「设置 › 邮件」: Outlook sign-in and the email rules (locked until 「✎ 修改」, see settings.js).
// The app's credentials and the rules are saved together; signing in, pasting a refresh token
// and disconnecting act right away, but only while the tab is unlocked.

const RULE_LABELS = { account: "账户", currency: "币种", category: "种类", sender: "发件人", subject: "标题包含",
                      date_re: "日期（正则）", merchant_re: "描述（正则）", amount_re: "金额（正则）" };
// A new rule starts with the patterns of a typical Japanese card notice
const NEW_RULE = { account: "", currency: "日元", category: "饮食费", sender: "", subject: "",
                   date_re: String.raw`利用日時\s+([0-9/:\s]+)`, merchant_re: String.raw`利用店舗\s+([^\n]+)`,
                   amount_re: String.raw`利用金額\s+([0-9,]+)円` };
let emCfg = null;
// While editing: typed credentials (undefined: untouched), typed rule fields by rule id,
// rule ids to delete, new rules
let emEdit = null;
async function loadEmailSettings() {
  try { emCfg = await api("/api/email"); } catch (e) { return flash("em-status", "读取失败：" + e.message); }
  renderEmail();
}
const CRED_INPUTS = { client_id: "em-client-id", secret: "em-secret", redirect: "em-redirect-uri" };
const credTyped = k => emEdit?.cred[k];
function credChanges() {
  const out = [];
  const id = credTyped("client_id")?.trim(), redirect = credTyped("redirect")?.trim(), secret = credTyped("secret")?.trim();
  if (id !== undefined && id !== emCfg.client_id)
    out.push(`Client ID 改为「${id}」${emCfg.connected ? "（会断开 Outlook，要重新连接）" : ""}`);
  if (secret) out.push("换一个 Client secret");
  if (redirect !== undefined && redirect !== emCfg.redirect_uri)
    out.push(redirect ? `回调地址改为「${redirect}」` : "回调地址改回默认");
  return out;
}
function renderEmail() {
  if (!emCfg) return;
  renderCredentials();
  renderRules();
}
function renderCredentials() {
  const cfg = emCfg, locked = !emEdit;
  $("em-redirect").textContent = cfg.default_redirect_uri;
  $("em-redirect-uri").placeholder = cfg.default_redirect_uri;
  const saved = { client_id: cfg.client_id, secret: "", redirect: cfg.redirect_uri };
  for (const [k, id] of Object.entries(CRED_INPUTS)) {
    if (document.activeElement !== $(id)) $(id).value = credTyped(k) ?? saved[k];
    $(id).disabled = locked;
  }
  $("em-secret").placeholder = cfg.has_secret ? "已保存（不显示）；要换就填新的" : "";
  const unsaved = credChanges().length > 0;
  $("em-connect").textContent = cfg.connected ? "重新连接" : "连接 Outlook";
  $("em-connect").disabled = $("em-save-token").disabled = locked || unsaved || !cfg.client_id || !cfg.has_secret;
  $("em-connect").title = unsaved ? "先保存 Client ID / secret / 回调地址" : "";
  $("em-disconnect").hidden = !cfg.connected;
  $("em-disconnect").disabled = $("em-token").disabled = locked;
  if (!$("em-conn").dataset.msg) $("em-conn").textContent = cfg.connected ? "✓ 已连接" : (cfg.client_id && cfg.has_secret ? "还没连接" : "先填 Client ID 和 Client secret");
}
function ruleFields(r, key, typed = {}) {
  const dis = !emEdit || emEdit.deleted.has(key) ? "disabled" : "";
  const input = (k, extra = "") => `<label>${RULE_LABELS[k]}<input data-rule="${key}" data-k="${k}" value="${esc(typed[k] ?? r[k])}" spellcheck="false" ${dis} ${extra}></label>`;
  return `<div class="settings">${input("account", 'list="m-accounts"')}${input("currency")}${input("category", 'list="m-categories"')}` +
    `${input("sender", 'placeholder="notice@example.com 或 @example.com"')}${input("subject", 'placeholder="空着表示不限"')}</div>` +
    `<div class="patterns">${input("date_re")}${input("merchant_re")}${input("amount_re")}</div>`;
}
function renderRules() {
  const rules = emCfg.rules;
  $("em-rules").innerHTML = rules.map((r, i) => {
    const gone = emEdit?.deleted.has(r.id);
    const btn = !emEdit ? "" : gone ? `<button class="secondary" data-rule-undel="${r.id}">↺ 撤销删除</button>`
                                    : `<button class="danger" data-rule-del="${r.id}">删除规则</button>`;
    return `<div class="rule ${gone ? "deleted" : ""}">${ruleFields(r, r.id, emEdit?.rules[r.id])}
      <div class="bar"><span class="status">规则 ${i + 1} · 已读 ${r.emails} 封邮件${gone ? " · 保存后删除" : ""}</span>${btn}</div></div>`;
  }).join("") +
    (emEdit?.added ?? []).map((r, i) => `<div class="rule draft">${ruleFields(r, "new" + i)}
      <div class="bar"><span class="status">新规则（保存后添加）</span><button class="secondary" data-rule-unadd="${i}">不添加了</button></div></div>`).join("") ||
    `<div class="empty">还没有规则</div>`;
  $("em-add-rule").hidden = !emEdit;
  const keep = $("em-test-rule").value;
  $("em-test-rule").innerHTML = rules.map((r, i) => `<option value="${r.id}">规则 ${i + 1}：${esc(r.sender)}</option>`).join("") +
    (emEdit?.added ?? []).map((r, i) => `<option value="new${i}">新规则 ${i + 1}（未保存）</option>`).join("");
  if ([...$("em-test-rule").options].some(o => o.value === keep)) $("em-test-rule").value = keep;
}
// Rule edits: { text, ... } in the order they are saved
function ruleChanges() {
  const out = [];
  emCfg.rules.forEach((r, i) => {
    if (emEdit.deleted.has(r.id)) return out.push({ text: `删除规则 ${i + 1}（${r.sender}）`, del: r.id });
    const fields = {};
    for (const [k, v] of Object.entries(emEdit.rules[r.id] ?? {})) {
      if (v.trim() === (r[k] ?? "")) continue;
      fields[k] = v;
      out.push({ text: `规则 ${i + 1} 的${RULE_LABELS[k]}：「${r[k]}」→「${v.trim()}」` });
    }
    if (Object.keys(fields).length) out.push({ update: { id: r.id, fields } });
  });
  for (const r of emEdit.added) out.push({ text: `新增规则（发件人「${r.sender.trim() || "未填"}」）`, add: r });
  return out;
}
lockable("email", {
  label: "邮件设置",
  editing: () => !!emEdit,
  begin() { if (emCfg) { emEdit = { cred: {}, rules: {}, deleted: new Set(), added: [] }; renderEmail(); } },
  end() { emEdit = null; renderEmail(); },
  changes: () => [...credChanges(), ...ruleChanges().filter(c => c.text).map(c => c.text)],
  save() {
    const body = {}, rules = ruleChanges();
    if (credChanges().length) body.credentials = {
      client_id: (credTyped("client_id") ?? emCfg.client_id).trim(),
      client_secret: credTyped("secret")?.trim() || null,
      redirect_uri: credTyped("redirect")?.trim() ?? null,
    };
    body.delete_rules = rules.filter(c => c.del).map(c => c.del);
    body.update_rules = rules.filter(c => c.update).map(c => c.update);
    body.add_rules = rules.filter(c => c.add).map(c => c.add);
    $("em-conn").dataset.msg = "";
    return api("/api/email/settings", send("PUT", body));
  },
  reload: loadEmailSettings,
});
for (const [k, id] of Object.entries(CRED_INPUTS)) $(id).addEventListener("input", e => {
  if (!emEdit) return;
  emEdit.cred[k] = e.target.value;
  lockMsg = "";
  renderLock();
  renderCredentials(); // 「连接」 waits until the credentials are saved
});
$("em-connect").onclick = () => { location.href = "/api/email/authorize"; };
$("em-save-token").onclick = async () => {
  $("em-save-token").disabled = true;
  try {
    await api("/api/email/token", send("PUT", { refresh_token: $("em-token").value }));
    $("em-token").value = "";
    $("em-conn").dataset.msg = "1";
    $("em-conn").textContent = "✓ token 能用，已连接";
  } catch (e) {
    $("em-conn").dataset.msg = "1";
    $("em-conn").textContent = "token 不能用：" + e.message;
  }
  loadEmailSettings();
};
$("em-disconnect").onclick = async () => {
  if (!confirm("断开 Outlook？之后要重新登录才能读邮件。已经读到的邮件和交易不受影响。")) return;
  await api("/api/email/disconnect", { method: "POST" }).catch(() => {});
  $("em-conn").dataset.msg = "";
  loadEmailSettings();
};
$("em-add-rule").onclick = () => {
  if (!emEdit) return;
  emEdit.added.push({ ...NEW_RULE });
  renderRules();
  renderLock();
  $("em-rules").querySelector(`[data-rule="new${emEdit.added.length - 1}"][data-k="account"]`)?.focus();
};
$("em-rules").addEventListener("input", e => {
  const { rule, k } = e.target.dataset;
  if (!rule || !emEdit) return;
  if (rule.startsWith("new")) emEdit.added[+rule.slice(3)][k] = e.target.value;
  else (emEdit.rules[rule] ??= {})[k] = e.target.value;
  lockMsg = "";
  renderLock();
});
$("em-rules").addEventListener("click", e => {
  if (!emEdit) return;
  const { ruleDel, ruleUndel, ruleUnadd } = e.target.dataset;
  if (ruleDel) emEdit.deleted.add(+ruleDel);
  else if (ruleUndel) emEdit.deleted.delete(+ruleUndel);
  else if (ruleUnadd !== undefined) emEdit.added.splice(+ruleUnadd, 1);
  else return;
  lockMsg = "";
  renderRules();
  renderLock();
});
$("em-test").onclick = async () => {
  const key = $("em-test-rule").value;
  if (!key) return flash("em-test-result", "先添加一条规则");
  // The regexes as they are in the inputs now, saved or not
  const fields = {};
  for (const k of ["date_re", "merchant_re", "amount_re"])
    fields[k] = $("em-rules").querySelector(`[data-rule="${key}"][data-k="${k}"]`)?.value ?? "";
  try {
    const { row, problem } = await api("/api/email/test", send("POST", { ...fields, text: $("em-test-text").value }));
    flash("em-test-result", row ? `✓ ${row.date}　${fmtAmount(row.amount)}　${row.merchant}` : `✗ ${problem}`);
  } catch (e) { flash("em-test-result", "✗ " + e.message); }
};
// Coming back from Microsoft's sign-in page
{
  const q = new URLSearchParams(location.search);
  const msg = q.has("email_connected") ? "✓ 已连接 Outlook" : q.get("email_error");
  if (msg) {
    $("em-conn").textContent = msg;
    $("em-conn").dataset.msg = "1";
    history.replaceState(null, "", location.pathname + location.hash);
  }
}
