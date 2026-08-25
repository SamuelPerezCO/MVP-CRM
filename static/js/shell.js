/* ---------------------------------------------------------------------------
 * Shell glue.
 *
 * HTMX swaps only part of the page, so any nav that sits *outside* the swapped
 * region has to move its own active state. That applies to two navs now:
 *   - the sidebar        (outside #content, never re-rendered)
 *   - the Inbox nav panel (inside #content, but only column 3 is swapped)
 *
 * Rather than special-case each, any container marked [data-nav-group] gets the
 * behaviour: clicking a [data-nav-item] inside it moves `is-active` to that item.
 *
 * Also handled here: document.title, which lives in <head>, outside every swap.
 *
 * All of this is progressive enhancement. With JS off every nav item is still a
 * plain link, and the server renders the correct active state on a full load.
 *
 * Note: listeners are delegated from `document` and elements are looked up on
 * demand rather than cached. A cached node goes stale the moment anything
 * re-renders its subtree, leaving clicks updating a detached element.
 * ------------------------------------------------------------------------- */
(function () {
  "use strict";

  var ACTIVE = "is-active";

  /** Move the active state to `item`, clearing its siblings in the same group. */
  function setActive(item) {
    if (!item) return;
    var group = item.closest("[data-nav-group]");
    if (!group) return;

    group.querySelectorAll("[data-nav-item]." + ACTIVE).forEach(function (el) {
      el.classList.remove(ACTIVE);
      el.removeAttribute("aria-current");
    });

    item.classList.add(ACTIVE);
    // The sidebar navigates between pages; the Inbox nav filters one list.
    item.setAttribute("aria-current", item.closest(".sidebar") ? "page" : "true");
  }

  // Instant feedback: don't wait for the fragment to come back.
  document.addEventListener("click", function (event) {
    setActive(event.target.closest("[data-nav-item]"));
  });

  /** Re-derive the sidebar's active icon from the address bar (back/forward). */
  function syncSidebarFromUrl() {
    var rail = document.querySelector(".sidebar");
    if (!rail) return;
    // "/" renders the default section but has its own URL, so map it across.
    var path =
      window.location.pathname === "/"
        ? rail.dataset.defaultHref
        : window.location.pathname;
    setActive(rail.querySelector('.nav-item[href="' + path + '"]'));
  }

  window.addEventListener("popstate", syncSidebarFromUrl);
  document.addEventListener("htmx:historyRestore", syncSidebarFromUrl);

  // Keep the tab title in step with whatever section is showing.
  function syncTitle() {
    var heading = document.querySelector("#content .page-head__title");
    if (heading) document.title = heading.textContent.trim() + " · MVP CRM";
  }

  document.addEventListener("htmx:afterSwap", syncTitle);
  document.addEventListener("htmx:historyRestore", syncTitle);
})();
