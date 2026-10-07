// Shared helpers: theme toggle, copy buttons, confirm prompts
(function () {
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("theme", t); } catch (e) {}
    document.dispatchEvent(new Event("themechange"));
  }
  document.addEventListener("click", function (e) {
    var tb = e.target.closest("[data-theme-toggle]");
    if (tb) {
      applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
      return;
    }
    var cb = e.target.closest("[data-copy]");
    if (cb) {
      var box = document.getElementById(cb.getAttribute("data-copy"));
      if (box) {
        box.select();
        var done = function () { var old = cb.textContent; cb.textContent = "Copied"; setTimeout(function () { cb.textContent = old; }, 1500); };
        if (navigator.clipboard) { navigator.clipboard.writeText(box.value).then(done); }
        else { document.execCommand("copy"); done(); }
      }
    }
  });
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); }
  });
})();
