/*
  AurumWeb client cabinet JavaScript
  Mobile-only helpers keep desktop navigation unchanged.
*/

(function () {
  var MOBILE_QUERY = "(max-width: 980px)";
  var contentTarget = document.querySelector("#client-content");
  var mobileMedia = window.matchMedia(MOBILE_QUERY);

  function isMobileCabinet() {
    return mobileMedia.matches;
  }

  function scrollToContent() {
    if (!contentTarget || !isMobileCabinet()) return;
    window.setTimeout(function () {
      contentTarget.scrollIntoView({ block: "start", behavior: "smooth" });
      if (contentTarget.focus) {
        contentTarget.focus({ preventScroll: true });
      }
    }, 80);
  }

  document.querySelectorAll("[data-client-nav] a").forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (!isMobileCabinet()) return;

      var target = new URL(link.href, window.location.href);
      if (!target.pathname.startsWith("/client/")) return;

      target.hash = "client-content";
      if (target.pathname === window.location.pathname) {
        event.preventDefault();
        history.replaceState(null, "", target.toString());
        scrollToContent();
        return;
      }

      event.preventDefault();
      window.location.href = target.toString();
    });
  });

  document.querySelectorAll("[data-mobile-content-form]").forEach(function (form) {
    form.addEventListener("submit", function () {
      if (!isMobileCabinet()) return;
      var input = form.querySelector("input[name='mobile_scroll']");
      if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = "mobile_scroll";
        input.value = "1";
        form.appendChild(input);
      }
    });
  });

  if (window.location.hash === "#client-content") {
    scrollToContent();
  }
})();

(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-project-tab]"));
  var panels = Array.prototype.slice.call(document.querySelectorAll("[data-project-panel]"));
  if (!tabs.length || !panels.length) return;

  var allowed = tabs.map(function (tab) { return tab.dataset.projectTab; });

  function activate(name, updateUrl) {
    if (allowed.indexOf(name) === -1) name = "overview";
    tabs.forEach(function (tab) {
      var active = tab.dataset.projectTab === name;
      tab.classList.toggle("is-active", active);
      if (active) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
    panels.forEach(function (panel) {
      panel.hidden = panel.dataset.projectPanel !== name;
    });
    if (updateUrl) history.replaceState(null, "", "#" + name);
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function (event) {
      event.preventDefault();
      activate(tab.dataset.projectTab, true);
    });
  });

  activate(window.location.hash.replace("#", "") || "overview", false);
})();
