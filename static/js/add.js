// 「记一笔」: switching between the ways of adding a record (screenshot / manual / email).

const METHODS = ["screenshot", "manual", "email"];
let method = METHODS.includes(load("tx.method")) ? load("tx.method") : "screenshot";
function showMethod(m) {
  method = m;
  save("tx.method", m);
  for (const x of METHODS) $("method-" + x).hidden = x !== m;
  for (const b of document.querySelectorAll("#view-add .tabs button")) b.classList.toggle("active", b.dataset.method === m);
  $("result").hidden = m === "manual" || (records.length === 0 && skipped.length === 0);
}
function setMethod(m) {
  showMethod(m);
  showImage(null);
  if (m !== "manual") loadPending();
  if (m === "email") loadEmailState();
  if (m === "manual") focusManual();
}
document.querySelector("#view-add .tabs").addEventListener("click", e => {
  const b = e.target.closest("[data-method]");
  if (b) setMethod(b.dataset.method);
});
