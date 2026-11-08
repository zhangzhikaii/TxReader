// 「记一笔 › 邮件导入」: reading new emails; their rows go to the same pending table.

let emailCfg = null;
const daysAgo = n => { const d = new Date(); d.setDate(d.getDate() - n); return ymd(d); };
async function loadEmailState() {
  try { emailCfg = await api("/api/email"); } catch (e) { return flash("e-status", "读取邮件设置失败：" + e.message); }
  const missing = [!emailCfg.connected && "连接 Outlook", !emailCfg.rules.length && "添加邮件规则"].filter(Boolean);
  $("e-setup").hidden = !missing.length;
  $("e-setup").innerHTML = missing.length ? `还要先到「<a href="#settings-email">设置 › 邮件</a>」${missing.join("、")}。` : "";
  $("e-fetch").disabled = missing.length > 0;
  $("e-last").textContent = emailCfg.last_fetch ? `上次更新到 ${fmtTime(emailCfg.last_fetch)}，这次读这之后收到的邮件` : "还没有从邮件更新过";
  $("e-start-field").hidden = !!emailCfg.last_fetch;
  if (!emailCfg.last_fetch && !$("e-start").value) {
    // The first update starts at the start month of the opening balances, else 30 days back
    try {
      const { start_month } = await api("/api/opening");
      $("e-start").value = start_month ? `${start_month}-01` : daysAgo(30);
    } catch { $("e-start").value = daysAgo(30); }
  }
  loadUnrecognized();
}
async function loadUnrecognized() {
  let emails;
  try { ({ emails } = await api("/api/email/unrecognized")); } catch { return; }
  $("e-failed").hidden = !emails.length;
  $("e-failed").querySelector("summary").textContent = `没认出来的邮件 ${emails.length} 封`;
  $("e-failed-rows").innerHTML = emails.map(m => `<tr>
    <td>${esc(fmtTime(m.received_at))}</td><td class="wrap">${esc(m.subject)}</td><td class="wrap">${esc(m.problem ?? "现在能认出来了，点下面的按钮")}</td>
    <td><button class="icon" data-view-mail="${m.email_id}" data-name="${esc(m.subject)}" title="查看原邮件">✉</button></td></tr>`).join("");
}
$("e-failed-rows").addEventListener("click", e => {
  const b = e.target.closest("[data-view-mail]");
  if (b) toggleEmail(+b.dataset.viewMail, b.dataset.name);
});
$("e-fetch").onclick = async () => {
  $("e-fetch").disabled = true;
  flash("e-status", "正在读取邮件…");
  try {
    const start = emailCfg?.last_fetch ? null : $("e-start").value || null;
    const data = await api("/api/email/fetch", send("POST", { start }));
    const c = data.counts;
    flash("e-status", `${fmtTime(data.since)} 到现在收到 ${c.read} 封邮件，符合规则的 ${c.matched} 封：新增 ${c.new} 条` +
      (c.seen ? `，${c.seen} 封以前读过（跳过）` : "") + (c.failed ? `，${c.failed} 封没认出来（见下面）` : ""));
    setPending(data.pending);
    loadCounts();
  } catch (e) {
    flash("e-status", "更新失败：" + e.message);
  }
  loadEmailState();
};
$("e-retry").onclick = async () => {
  $("e-retry").disabled = true;
  try {
    const data = await api("/api/email/retry", { method: "POST" });
    flash("e-status", `重新识别：新增 ${data.new} 条` + (data.failed ? `，还有 ${data.failed} 封认不出来` : ""));
    setPending(data.pending);
  } catch (e) {
    flash("e-status", "重新识别失败：" + e.message);
  }
  $("e-retry").disabled = false;
  loadUnrecognized();
};
