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
 * Also handled here: document.title, which lives in <head>, outside every swap,
 * and the welcome screen's empty state -- "/" selects no icon at all, so the
 * rail has to be cleared rather than pointed at a default.
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

  /** Drop the active state from every item in `group`. */
  function clearActive(group) {
    if (!group) return;
    group.querySelectorAll("[data-nav-item]." + ACTIVE).forEach(function (el) {
      el.classList.remove(ACTIVE);
      el.removeAttribute("aria-current");
    });
  }

  /** Move the active state to `item`, clearing its siblings in the same group. */
  function setActive(item) {
    if (!item) return;
    var group = item.closest("[data-nav-group]");
    if (!group) return;

    clearActive(group);

    item.classList.add(ACTIVE);
    // The sidebar navigates between pages; the Inbox nav filters one list.
    item.setAttribute("aria-current", item.closest(".sidebar") ? "page" : "true");
  }

  /** The sidebar rail, or null before it has rendered. */
  function getRail() {
    return document.querySelector(".sidebar");
  }

  // Instant feedback: don't wait for the fragment to come back.
  document.addEventListener("click", function (event) {
    var item = event.target.closest("[data-nav-item]");
    if (item) {
      setActive(item);
      return;
    }

    // Shortcuts that swap #content from outside the rail -- the welcome
    // screen's quick-access cards -- name the icon they correspond to, so the
    // rail can follow a navigation it did not originate.
    var shortcut = event.target.closest("[data-nav-for]");
    if (!shortcut) return;
    var rail = getRail();
    if (rail) {
      setActive(rail.querySelector('.nav-item[href="' + shortcut.dataset.navFor + '"]'));
    }
  });

  /** Re-derive the sidebar's active icon from the address bar (back/forward). */
  function syncSidebarFromUrl() {
    var rail = getRail();
    if (!rail) return;

    var match = rail.querySelector(
      '.nav-item[href="' + window.location.pathname + '"]'
    );

    // "/" is the welcome screen and matches no icon. Clear the rail rather than
    // leaving the previous section's pill lit -- setActive(null) is a no-op, so
    // the empty case has to be handled explicitly.
    if (match) setActive(match);
    else clearActive(rail);
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
