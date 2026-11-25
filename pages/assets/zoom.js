// Click an animation (GIF) or a step-by-step picture to see it bigger in a dialog.
// A GIF opens its sharper <name>_large.webp next to it, or the GIF itself if that is missing.
(function () {
  function zoomable(img) {
    var src = img.getAttribute("src") || "";
    return /\.gif$/i.test(src) || /\/howto\/steps\//.test(src);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var imgs = Array.prototype.filter.call(document.querySelectorAll("img"), zoomable);
    if (!imgs.length || !window.HTMLDialogElement) return;

    var dlg = document.createElement("dialog");
    dlg.className = "zoom";
    dlg.innerHTML = '<button type="button" class="zoom-close" aria-label="Close">&times;</button>' +
                    '<img alt=""><p class="zoom-cap"></p>';
    document.body.appendChild(dlg);
    var big = dlg.querySelector("img");
    var cap = dlg.querySelector(".zoom-cap");

    imgs.forEach(function (img) {
      img.classList.add("zoomable");
      img.tabIndex = 0;
      img.title = "Click to enlarge";
      function open() {
        var src = img.currentSrc || img.src;
        big.onerror = function () { big.onerror = null; big.src = src; };
        big.src = src.replace(/\.gif$/i, "_large.webp");
        big.alt = img.alt;
        cap.textContent = img.alt;
        dlg.showModal();
      }
      img.addEventListener("click", open);
      img.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    });

    // a click on the backdrop, the picture or the close button closes it; Esc does too
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg || e.target === big || e.target.closest(".zoom-close")) dlg.close();
    });
    dlg.addEventListener("close", function () { big.removeAttribute("src"); });
  });
})();
