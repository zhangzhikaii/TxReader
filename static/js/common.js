// Shared by every page: DOM / storage / request helpers, formatting.

const $ = id => document.getElementById(id);

const load = k => { try { return localStorage.getItem(k); } catch { return null; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("请先登录"); }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : res;
  if (!res.ok) {
    const err = new Error((isJson && (data.detail?.message ?? data.detail)) || res.statusText);
    err.status = res.status;
    err.detail = isJson ? data.detail : null;
    throw err;
  }
  return data;
}
const send = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const WEEKDAYS_JA = "日月火水木金土"; // indexed by Date.getDay()
const fmtAmount = n => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Accepts "-35", "(35)" (negative, the way fmtLedger writes it), "1,234.5", "¥35"
const parseAmount = s => {
  const t = String(s).replace(/[,\s¥]/g, ""), paren = /^\(.*\)$/.test(t);
  const v = parseFloat(paren ? t.slice(1, -1) : t) || 0;
  return paren ? -Math.abs(v) : v;
};
// Ledger style for transaction amounts: expenses in parentheses (red), income green; see amtClass
const fmtLedger = n => n < 0 ? `(${fmtAmount(-n)})` : fmtAmount(n);
const amtClass = n => n < 0 ? "amt-out" : n > 0 ? "amt-in" : "";
const fmtTime = iso => iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "";
const pad = n => String(n).padStart(2, "0");

// Accepts 2026/10/29 22:12:00, 2026-10-29 22:12, 2026/10/29 ...; returns normalized string or null
function parseDate(s) {
  const m = s.trim().match(/^(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
  if (!m) return null;
  const [y, mo, d, h = 0, mi = 0, se = 0] = m.slice(1).map(v => v === undefined ? undefined : +v);
  const dt = new Date(y, mo - 1, d, h, mi, se);
  if (dt.getMonth() !== mo - 1 || dt.getDate() !== d || h > 23 || mi > 59 || se > 59) return null;
  return { date: `${y}/${pad(mo)}/${pad(d)} ${pad(h)}:${pad(mi)}:${pad(se)}`, weekday: WEEKDAYS_JA[dt.getDay()] };
}

function flash(el, msg) { $(el).textContent = msg; }

const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fmtMonth = m => `${m.slice(0, 4)}年${+m.slice(5)}月`;
const signed = v => (v > 0 ? "+" : "") + fmtAmount(v);
const monthEnd = m => { const d = new Date(+m.slice(0, 4), +m.slice(5), 0); return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`; };
// A money cell: red when negative, grey when zero, a warning when it couldn't be worked out
function moneyCell(v, missing = "缺汇率") {
  if (v === undefined) return `<td class="none">—</td>`;
  if (v === null) return `<td class="missing">${missing}</td>`;
  return `<td class="num ${v < 0 ? "neg" : v === 0 ? "zero" : ""}">${fmtAmount(v)}</td>`;
}

// Spreadsheet-like keys in a table of inputs: Enter moves to the same column in the next row
// (Shift+Enter: the previous one), and a cell's text is selected when focus moves into it.
// column(el): a selector matching the inputs of el's column, or null when el isn't a cell.
// atEnd(el, e): no row further that way; by default leave the cell, which saves it.
function tableKeys(tbody, column, atEnd = el => el.blur()) {
  tbody.addEventListener("keydown", e => {
    if (e.key !== "Enter" || e.isComposing) return; // isComposing: Enter confirming IME input
    const sel = column(e.target);
    if (!sel) return;
    e.preventDefault();
    const cells = [...tbody.querySelectorAll(sel)];
    const next = cells[cells.indexOf(e.target) + (e.shiftKey ? -1 : 1)];
    if (next) next.focus(); else atEnd(e.target, e);
  });
  tbody.addEventListener("focusin", e => {
    if (column(e.target) && e.target.type !== "datetime-local") e.target.select();
  });
}
