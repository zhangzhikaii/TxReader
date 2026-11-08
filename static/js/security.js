// 「登录记录」: signed-in devices and recent sign-in attempts.

const REASONS = { ok: "成功", bad_password: "密码错误", locked: "被锁定（错误次数太多）", not_configured: "服务器未设置密码" };
async function loadSecurity() {
  let data;
  try { data = await api("/api/security"); } catch { return; }
  $("logout").hidden = !data.auth;
  $("sec-off").hidden = data.auth;
  $("sec-logout-all").hidden = !data.auth;
  $("sec-sessions").innerHTML = data.sessions.map(s =>
    `<tr><td>${esc(fmtTime(s.created_at))}</td><td>${esc(fmtTime(s.last_seen_at))}</td>` +
    `<td title="一段时间不操作会自动退出登录，继续使用会顺延">${esc(fmtTime(s.ends_at))}</td><td>${esc(s.ip)}</td>` +
    `<td class="wrap">${esc(s.user_agent)}</td><td>${s.current ? '<span class="tag done">本机</span>' : ""}</td></tr>`).join("");
  $("sec-events").innerHTML = data.events.map(ev =>
    `<tr><td>${esc(fmtTime(ev.at))}</td><td>${ev.success ? "" : "❌ "}${esc(REASONS[ev.reason] ?? ev.reason)}</td>` +
    `<td>${esc(ev.ip)}</td><td class="wrap">${esc(ev.user_agent)}</td></tr>`).join("") ||
    `<tr><td colspan="4" class="empty">没有记录</td></tr>`;
}
$("sec-logout-all").onclick = async () => {
  if (!confirm("所有设备（包括这台）都会退出登录，需要重新输入密码。继续吗？")) return;
  await api("/api/logout-all", { method: "POST" }).catch(() => {});
  location.href = "/login";
};
