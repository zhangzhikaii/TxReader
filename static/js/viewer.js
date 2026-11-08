// The panel on the right showing a screenshot or an email, and the thumbnails of this session's uploads.

// Uploaded screenshots: thumbnails under the drop zone, click to view on the right
function renderThumbs() {
  $("thumbs").innerHTML = images.map(im =>
    `<div class="thumb">` +
    `<button class="thumb-open ${im.key === viewing ? "active" : ""}" data-img="${im.key}" title="${esc(im.name)}">` +
    `<img src="${im.url}" alt="${esc(im.name)}"></button>` +
    `<button class="thumb-del" data-img-del="${im.key}" title="从这里移除（已识别的记录保留）" aria-label="移除截图">×</button>` +
    `</div>`).join("");
}
function removeImage(key) {
  const im = images.find(i => i.key === key);
  if (!im) return;
  URL.revokeObjectURL(im.url);
  images = images.filter(i => i !== im);
  if (viewing === key) showImage(null);
  else renderThumbs();
}
// im: { key, name, url } for a screenshot, { key, name, text } for an email, or null to close
function showImage(im) {
  viewing = im ? im.key : null;
  document.body.classList.toggle("viewing", !!im);
  if (im) {
    const isText = im.text != null;
    $("viewer-img").hidden = isText;
    $("viewer-text").hidden = !isText;
    if (isText) {
      $("viewer-text").textContent = im.text;
      $("viewer-img").removeAttribute("src");
    } else {
      $("viewer-img").src = im.url;
      $("viewer-img").alt = im.name;
    }
    $("viewer-name").textContent = im.name;
    $("viewer").scrollTop = 0;
  }
  renderThumbs();
  for (const b of document.querySelectorAll("[data-view-img]")) b.classList.toggle("active", viewing === "srv-" + b.dataset.viewImg);
  for (const b of document.querySelectorAll("[data-view-mail]")) b.classList.toggle("active", viewing === "mail-" + b.dataset.viewMail);
  for (const tr of document.querySelectorAll("tr[data-img-id]")) tr.classList.toggle("viewing", viewing === "srv-" + tr.dataset.imgId);
  for (const tr of document.querySelectorAll("tr[data-mail-id]")) tr.classList.toggle("viewing", viewing === "mail-" + tr.dataset.mailId);
}
function toggleServerImage(id, name) {
  const key = "srv-" + id;
  showImage(viewing === key ? null : { key, name: name || `截图 #${id}`, url: `/api/images/${id}` });
}
async function toggleEmail(id, name) {
  const key = "mail-" + id;
  if (viewing === key) return showImage(null);
  try {
    const m = await api(`/api/emails/${id}`);
    showImage({ key, name: m.subject || name || `邮件 #${id}`,
                text: `发件人：${m.sender}\n收到：${fmtTime(m.received_at)}\n\n${m.body}` });
  } catch (e) {
    alert("打不开这封邮件：" + e.message);
  }
}
$("thumbs").addEventListener("click", e => {
  const del = e.target.closest("[data-img-del]")?.dataset.imgDel;
  if (del) return removeImage(del);
  const key = e.target.closest("[data-img]")?.dataset.img;
  if (key) showImage(key === viewing ? null : images.find(i => i.key === key)); // clicking the open one closes it
});
$("viewer-close").onclick = () => showImage(null);
document.addEventListener("keydown", e => { if (e.key === "Escape" && viewing) showImage(null); });
