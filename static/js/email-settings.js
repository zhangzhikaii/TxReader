// 「设置 › 邮件」: Outlook sign-in and the email rules.

const RULE_LABELS = { account: "账户", currency: "币种", category: "种类", sender: "发件人", subject: "标题包含",
                      date_re: "日期（正则）", merchant_re: "描述（正则）", amount_re: "金额（正则）" };
// A new rule starts with the patterns of a typical Japanese card notice
const NEW_RULE = { account: "", currency: "日元", category: "饮食费", sender: "", subject: "",
                   date_re: String.raw`利用日時\s+([0-9/:\s]+)`, merchant_re: String.raw`利用店舗\s+([^\n]+)`,
                   amount_re: String.raw`利用金額\s+([0-9,]+)円` };
let emailRules = [];
let draftRule = null; // the rule being added, not saved yet
async function loadEmailSettings() {
  let cfg;
  try { cfg = await api("/api/email"); } catch (e) { return flash("em-status", "读取失败：" + e.message); }
  $("em-redirect").textContent = cfg.default_redirect_uri;
  if (document.activeElement !== $("em-redirect-uri")) $("em-redirect-uri").value = cfg.redirect_uri;
  $("em-redirect-uri").placeholder = cfg.default_redirect_uri;
  if (document.activeElement !== $("em-client-id")) $("em-client-id").value = cfg.client_id;
  $("em-secret").value = "";
  $("em-secret").placeholder = cfg.has_secret ? "已保存（不显示）；要换就填新的" : "";
  $("em-connect").textContent = cfg.connected ? "重新连接" : "连接 Outlook";
  $("em-connect").disabled = $("em-save-token").disabled = !cfg.client_id || !cfg.has_secret;
  $("em-disconnect").hidden = !cfg.connected;
  if (!$("em-conn").dataset.msg) $("em-conn").textContent = cfg.connected ? "✓ 已连接" : (cfg.client_id && cfg.has_secret ? "还没连接" : "先填 Client ID 和 Client secret");
  emailRules = cfg.rules;
  renderRules();
}
function ruleFields(r, key) {
  const input = (k, extra = "") => `<label>${RULE_LABELS[k]}<input data-rule="${key}" data-k="${k}" value="${esc(r[k])}" spellcheck="false" ${extra}></label>`;
  return `<div class="settings">${input("account", 'list="m-accounts"')}${input("currency")}${input("category", 'list="m-categories"')}` +
    `${input("sender", 'placeholder="notice@example.com 或 @example.com"')}${input("subject", 'placeholder="空着表示不限"')}</div>` +
    `<div class="patterns">${input("date_re")}${input("merchant_re")}${input("amount_re")}</div>`;
}
function renderRules() {
  $("em-rules").innerHTML = emailRules.map((r, i) => `<div class="rule">
      ${ruleFields(r, r.id)}
      <div class="bar"><span class="status">规则 ${i + 1} · 已读 ${r.emails} 封邮件</span>
        <button class="danger" data-rule-del="${r.id}">删除规则</button></div></div>`).join("") +
    (draftRule ? `<div class="rule draft">${ruleFields(draftRule, "new")}
      <div class="bar"><button data-rule-save>保存规则</button><button class="secondary" data-rule-cancel>取消</button></div></div>` : "") ||
    `<div class="empty">还没有规则</div>`;
  $("em-add-rule").hidden = !!draftRule;
  const keep = $("em-test-rule").value;
  $("em-test-rule").innerHTML = emailRules.map((r, i) => `<option value="${r.id}">规则 ${i + 1}：${esc(r.sender)}</option>`).join("") +
    (draftRule ? `<option value="new">新规则（未保存）</option>` : "");
  if ([...$("em-test-rule").options].some(o => o.value === keep)) $("em-test-rule").value = keep;
}
$("em-save-cred").onclick = async () => {
  const secret = $("em-secret").value.trim();
  try {
    await api("/api/email/credentials", send("PUT", { client_id: $("em-client-id").value.trim(), client_secret: secret || null,
                                                       redirect_uri: $("em-redirect-uri").value.trim() }));
    flash("em-status", "");
    $("em-conn").dataset.msg = "";
    $("em-conn").textContent = "已保存";
  } catch (e) { $("em-conn").textContent = "保存失败：" + e.message; }
  loadEmailSettings();
};
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
  $("em-save-token").disabled = false;
  loadEmailSettings();
};
$("em-disconnect").onclick = async () => {
  if (!confirm("断开 Outlook？之后要重新登录才能读邮件。已经读到的邮件和交易不受影响。")) return;
  await api("/api/email/disconnect", { method: "POST" }).catch(() => {});
  $("em-conn").dataset.msg = "";
  loadEmailSettings();
};
$("em-add-rule").onclick = () => { draftRule = { ...NEW_RULE }; renderRules(); $("em-rules").querySelector('[data-rule="new"][data-k="account"]')?.focus(); };
$("em-rules").addEventListener("input", e => {
  const { rule, k } = e.target.dataset;
  if (rule === "new" && draftRule) draftRule[k] = e.target.value;
});
$("em-rules").addEventListener("change", async e => {
  const { rule, k } = e.target.dataset;
  if (!rule || rule === "new") return;
  const r = emailRules.find(x => x.id === +rule);
  try {
    await api(`/api/email/rules/${rule}`, send("PATCH", { [k]: e.target.value }));
    r[k] = e.target.value.trim();
    e.target.classList.remove("invalid");
    flash("em-status", `已保存${RULE_LABELS[k]}`);
  } catch (err) {
    e.target.classList.add("invalid");
    flash("em-status", "没有保存：" + err.message);
  }
});
$("em-rules").addEventListener("click", async e => {
  if (e.target.matches("[data-rule-cancel]")) { draftRule = null; renderRules(); return; }
  if (e.target.matches("[data-rule-save]")) {
    try {
      await api("/api/email/rules", send("POST", draftRule));
      draftRule = null;
      flash("em-status", "已添加规则");
      loadEmailSettings();
    } catch (err) { flash("em-status", "没有保存：" + err.message); }
    return;
  }
  const del = e.target.dataset.ruleDel;
  if (del && confirm("删除这条规则？已经读到的邮件和交易不受影响，以后不再读这种邮件。")) {
    try { await api(`/api/email/rules/${del}`, { method: "DELETE" }); flash("em-status", "已删除规则"); }
    catch (err) { flash("em-status", "删除失败：" + err.message); }
    loadEmailSettings();
  }
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
