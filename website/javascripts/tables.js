// A table or diagram wider than the page scrolls sideways inside its own box. Make that box
// reachable from the keyboard (arrow keys scroll it once it has focus) and give it a name, taken
// from the nearest heading before it. Material calls document$ on every page load, including
// instant navigation; diagrams are drawn later, so they are checked as they appear.
(function () {
  function heading(el) {
    for (var node = el; node && !node.classList.contains("md-content__inner"); node = node.parentElement) {
      for (var prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) {
        if (/^H[1-6]$/.test(prev.tagName)) return prev.textContent.replace(/¶$/, "").trim();
      }
    }
    return "";
  }

  function mark(el, kind) {
    var scrolls = el.scrollWidth > el.clientWidth + 1;
    if (scrolls && !el.dataset.ayScroll) {
      var title = heading(el);
      el.setAttribute("tabindex", "0");
      el.setAttribute("role", "region");
      el.setAttribute("aria-label", (title ? title + ": " : "") + kind + ", scrolls sideways");
      el.dataset.ayScroll = "1";
    } else if (!scrolls && el.dataset.ayScroll) {
      ["tabindex", "role", "aria-label", "data-ay-scroll"].forEach(function (a) { el.removeAttribute(a); });
    }
  }

  function scan() {
    document.querySelectorAll(".md-typeset__scrollwrap").forEach(function (el) { mark(el, "table"); });
    document.querySelectorAll("div.mermaid").forEach(function (el) { mark(el, "diagram"); });
  }

  var observer = null;
  document$.subscribe(function () {
    scan();
    if (observer) observer.disconnect();
    var content = document.querySelector(".md-content");
    if (!content || !window.MutationObserver) return;
    observer = new MutationObserver(scan);
    observer.observe(content, { childList: true, subtree: true });
  });
  window.addEventListener("resize", scan);
})();
