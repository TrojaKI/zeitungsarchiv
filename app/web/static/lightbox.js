// Minimal zoom/pan lightbox for scan images. No dependencies.
// Any <img class="zoomable"> opens in a fullscreen overlay:
//   - mouse wheel zooms toward the cursor
//   - drag pans
//   - double-click resets zoom
//   - Esc or background click closes
(function () {
  "use strict";

  let overlay, img, scale = 1, tx = 0, ty = 0, dragging = false, lastX = 0, lastY = 0;

  function build() {
    overlay = document.createElement("div");
    overlay.className = "lightbox-overlay";
    img = document.createElement("img");
    img.className = "lightbox-img";
    overlay.appendChild(img);
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    overlay.addEventListener("wheel", onWheel, { passive: false });
    img.addEventListener("dblclick", resetTransform);
    img.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", function () { dragging = false; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });
  }

  function apply() {
    img.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
  }

  function resetTransform() {
    scale = 1; tx = 0; ty = 0; apply();
  }

  function open(src) {
    if (!overlay) build();
    img.src = src;
    resetTransform();
    overlay.classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function close() {
    overlay.classList.remove("open");
    document.body.style.overflow = "";
  }

  function onWheel(e) {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const next = Math.min(Math.max(scale * factor, 1), 8);
    // zoom toward cursor position relative to image centre
    const rect = img.getBoundingClientRect();
    const cx = e.clientX - (rect.left + rect.width / 2);
    const cy = e.clientY - (rect.top + rect.height / 2);
    tx -= cx * (next / scale - 1);
    ty -= cy * (next / scale - 1);
    scale = next;
    if (scale === 1) { tx = 0; ty = 0; }
    apply();
  }

  function onDown(e) {
    if (scale === 1) return;
    dragging = true;
    lastX = e.clientX; lastY = e.clientY;
    e.preventDefault();
  }

  function onMove(e) {
    if (!dragging) return;
    tx += e.clientX - lastX;
    ty += e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    apply();
  }

  document.addEventListener("click", function (e) {
    const target = e.target.closest("img.zoomable");
    if (target) {
      e.preventDefault();
      open(target.dataset.full || target.src);
    }
  });
})();
