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

  /* -------------------------------------------------------------------------
   * Dismissible banners.
   *
   * Any element with [data-dismissible="<key>"] can contain a [data-dismiss]
   * button. Clicking it hides the element and records the key in localStorage,
   * so the banner stays gone across reloads -- and across HTMX swaps, which
   * re-render it fresh from the server: after every swap the stored keys are
   * re-applied before the user sees the frame settle.
   *
   * localStorage access is wrapped because it can throw (private windows,
   * blocked site data); in that case the banner simply returns next page load.
   * ---------------------------------------------------------------------- */

  var DISMISS_PREFIX = "dismissed:";

  function isDismissed(key) {
    try { return window.localStorage.getItem(DISMISS_PREFIX + key) === "1"; }
    catch (e) { return false; }
  }

  function rememberDismissed(key) {
    try { window.localStorage.setItem(DISMISS_PREFIX + key, "1"); }
    catch (e) { /* no persistence available -- hide for this view only */ }
  }

  /** Hide every banner in `root` the user has already dismissed. */
  function hideDismissed(root) {
    (root || document).querySelectorAll("[data-dismissible]").forEach(function (el) {
      if (isDismissed(el.dataset.dismissible)) el.hidden = true;
    });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-dismiss]");
    if (!btn) return;
    var banner = btn.closest("[data-dismissible]");
    if (!banner) return;
    banner.hidden = true;
    rememberDismissed(banner.dataset.dismissible);
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    hideDismissed(event.target);
  });
  document.addEventListener("htmx:historyRestore", function () {
    hideDismissed(document);
  });

  // First paint: the script is deferred, so the DOM is already parsed.
  hideDismissed(document);

  /* -------------------------------------------------------------------------
   * Chat scroll position.
   *
   * The Inbox thread (#chat-messages) re-renders every few seconds (poll) and
   * on every send. A chat should rest at the newest message, so scroll to the
   * bottom after those swaps -- but only when the user was already there:
   * someone scrolled up reading history must not be yanked back down by a
   * poll. "There" is measured just before the swap, with a small tolerance.
   * ---------------------------------------------------------------------- */

  var chatWasAtBottom = true;

  function isChatBox(el) {
    return el && el.id === "chat-messages";
  }

  function nearBottom(box) {
    return box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  }

  document.addEventListener("htmx:beforeSwap", function (event) {
    if (isChatBox(event.detail.target)) chatWasAtBottom = nearBottom(event.detail.target);
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    var box = isChatBox(event.detail.target)
      ? event.detail.target
      : event.detail.target.querySelector("#chat-messages"); // thread just opened
    if (!box) return;
    // A freshly-opened thread always starts at the newest message.
    if (!isChatBox(event.detail.target) || chatWasAtBottom) {
      box.scrollTop = box.scrollHeight;
      chatWasAtBottom = true;
    }
  });

  // Full-page load with ?chat= in the URL renders the thread server-side.
  var initialChat = document.getElementById("chat-messages");
  if (initialChat) initialChat.scrollTop = initialChat.scrollHeight;

  /* -------------------------------------------------------------------------
   * Dialogs (the Etiquetas create/edit modals, the template chooser).
   *
   * Any [data-dialog-open="<id>"] control opens the <dialog> with that id;
   * [data-dialog-close] closes the enclosing one. showModal() supplies the
   * focus trap, Escape handling and focus return natively. A dialog marked
   * [data-dialog-backdrop-close] also closes on a backdrop click, and any
   * element marked [data-close-on-success] closes its enclosing dialog once
   * its HTMX request succeeds (forms additionally reset) -- the response has
   * already re-rendered the region behind it.
   * ---------------------------------------------------------------------- */

  // True while the last pointer press began on a backdrop-close dialog's
  // ::backdrop. A click alone can't tell: a drag that starts on the dialog's
  // content and ends past its edge also reports the dialog as target with
  // outside coordinates, and only the press location separates the two.
  var pressOnBackdrop = false;

  function outsideDialogBox(dialog, event) {
    var box = dialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right ||
           event.clientY < box.top || event.clientY > box.bottom;
  }

  document.addEventListener("pointerdown", function (event) {
    pressOnBackdrop =
      event.target.matches &&
      event.target.matches("dialog[data-dialog-backdrop-close]") &&
      event.target.open &&
      outsideDialogBox(event.target, event);
  });

  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      var dialog = document.getElementById(opener.dataset.dialogOpen);
      // A screen's own script may have opened it already (showModal on an
      // open dialog throws).
      if (dialog && !dialog.open) dialog.showModal();
      return;
    }
    var closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      var enclosing = closer.closest("dialog");
      if (enclosing) enclosing.close();
      return;
    }
    // A ::backdrop click reports the <dialog> itself as target -- but so do
    // clicks on its padding and drags that end past its edge, so only close
    // when the press started on the backdrop and the release stayed outside.
    // detail === 1 skips the tail of a double-click on the opener (its second
    // press lands on the backdrop of the freshly-opened dialog) as well as
    // keyboard-synthesized clicks, which report detail 0 at (0,0).
    var backdrop = event.target;
    if (backdrop.matches && backdrop.matches("dialog[data-dialog-backdrop-close]") &&
        backdrop.open && event.detail === 1 && pressOnBackdrop &&
        outsideDialogBox(backdrop, event)) {
      backdrop.close();
    }
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    var el = event.target.closest && event.target.closest("[data-close-on-success]");
    if (!el || !event.detail.successful) return;
    var dialog = el.closest("dialog");
    if (dialog && dialog.open) dialog.close();
    if (el.tagName === "FORM") {
      el.reset();
      syncTagPreview(el);
    }
  });

  // A dialog's card/link can swap away the very panel hosting the dialog --
  // and itself. The dialog is then simply removed (close() never runs on
  // that path), so the browser parks focus on <body> and announces nothing.
  // Hand focus to the fresh content's heading instead.
  document.addEventListener("htmx:afterSwap", function (event) {
    var config = event.detail.requestConfig;
    var origin = config && config.triggeringEvent && config.triggeringEvent.target;
    if (!origin || !origin.closest) return;
    if (
      !origin.closest("[data-close-on-success]") &&
      !origin.closest("[data-plantilla-form]")
    ) return;
    if (document.activeElement && document.activeElement !== document.body) return;
    // A failed editor submit lands on its first invalid field; any other
    // swap that dropped focus lands on the new content's heading.
    var target =
      event.detail.target.querySelector(".ffield--error .ffield__input") ||
      event.detail.target.querySelector("h1, h2");
    if (!target) return;
    if (!target.matches("input, select, textarea")) {
      target.setAttribute("tabindex", "-1");
    }
    target.focus();
  });

  /* -------------------------------------------------------------------------
   * Crear plantilla editor ([data-plantilla-form]): live name validation,
   * category-driven sub-type groups, header/button panels, character
   * counters, {{n}} variable insertion with sample inputs, and the WhatsApp
   * preview. Everything is delegated so it survives HTMX swaps; these rules
   * mirror core.plantillas, which re-validates every POST server-side.
   * ---------------------------------------------------------------------- */

  var TEMPLATE_NAME_RE = /^[a-z0-9_]+$/;
  // Canonical variables only ({{1}}, {{2}}... no leading zeros) -- mirrors
  // core.plantillas.VARIABLE_RE so client and server agree on what counts.
  var TEMPLATE_VARIABLE_RE = /\{\{([1-9]\d*)\}\}/g;
  var MEDIA_HEADER_LABELS = {
    image: "Imagen",
    video: "Video",
    document: "Documento",
  };

  function checkedValue(form, name) {
    var radio = form.querySelector(
      'input[name="' + name + '"]:checked:not(:disabled)'
    );
    return radio ? radio.value : "";
  }

  function bodyVariableNumbers(body) {
    var numbers = [];
    var match;
    TEMPLATE_VARIABLE_RE.lastIndex = 0;
    while ((match = TEMPLATE_VARIABLE_RE.exec(body)) !== null) {
      var number = parseInt(match[1], 10);
      if (numbers.indexOf(number) === -1) numbers.push(number);
    }
    return numbers;
  }

  // Meta's hard constraint: lowercase what was typed, flag what remains
  // invalid immediately (not only on submit).
  function syncTemplateName(input) {
    var pos = input.selectionStart;
    var lower = input.value.toLowerCase();
    if (lower !== input.value) {
      input.value = lower;
      if (pos !== null) input.setSelectionRange(pos, pos);
    }
    var invalid = input.value !== "" && !TEMPLATE_NAME_RE.test(input.value);
    var field = input.closest(".ffield");
    if (field) field.classList.toggle("ffield--error", invalid);
    input.setAttribute("aria-invalid", invalid ? "true" : "false");
  }

  // The sub-type options depend on the category: show the matching group,
  // disable the rest (disabled radios don't submit) and make sure the
  // visible group has a selection.
  function syncTemplateCategory(form) {
    var category = checkedValue(form, "category");
    form.querySelectorAll("[data-subtype-group]").forEach(function (group) {
      var active = group.dataset.subtypeGroup === category;
      group.hidden = !active;
      var radios = group.querySelectorAll('input[name="sub_type"]');
      radios.forEach(function (radio) { radio.disabled = !active; });
      if (active && !group.querySelector('input[name="sub_type"]:checked')) {
        if (radios[0]) radios[0].checked = true;
      }
    });
  }

  function syncTemplateHeader(form) {
    var kind = checkedValue(form, "header_type");
    var textPanel = form.querySelector('[data-header-panel="text"]');
    var mediaPanel = form.querySelector('[data-header-panel="media"]');
    if (textPanel) textPanel.hidden = kind !== "text";
    if (mediaPanel) mediaPanel.hidden = !MEDIA_HEADER_LABELS[kind];
    var label = form.querySelector("[data-header-media-label]");
    if (label && MEDIA_HEADER_LABELS[kind]) {
      label.textContent =
        "Sube " +
        (kind === "image" ? "una imagen" : kind === "video" ? "un video" : "un documento") +
        " de ejemplo";
    }
    // Match the file picker to the chosen kind (server re-checks the type).
    var upload = form.elements.header_media;
    if (upload) {
      upload.accept =
        kind === "image" ? "image/*" :
        kind === "video" ? "video/*" : "application/pdf";
    }
  }

  function syncTemplateButtons(form) {
    var kind = checkedValue(form, "button_kind");
    var quickPanel = form.querySelector('[data-buttons-panel="quick"]');
    var ctaPanel = form.querySelector('[data-buttons-panel="cta"]');
    if (quickPanel) quickPanel.hidden = kind !== "quick";
    if (ctaPanel) ctaPanel.hidden = kind !== "cta";
  }

  function syncTemplateCounters(form) {
    form.querySelectorAll("[data-counted-input]").forEach(function (input) {
      var field = input.closest(".ffield");
      var counter = field && field.querySelector("[data-count]");
      if (counter) {
        counter.textContent = input.value.length + "/" + input.maxLength;
      }
    });
  }

  // One sample input per {{n}} -- rebuilt as variables come and go, keeping
  // already-typed values by input name.
  function syncTemplateSamples(form) {
    var body = form.querySelector("[data-body-input]");
    var wrap = form.querySelector("[data-sample-list]");
    var host = form.querySelector("[data-sample-inputs]");
    if (!body || !wrap || !host) return;

    var kept = {};
    host.querySelectorAll("input").forEach(function (input) {
      kept[input.name] = input.value;
    });

    var numbers = bodyVariableNumbers(body.value);
    host.textContent = "";
    numbers.forEach(function (number) {
      var field = document.createElement("div");
      field.className = "ffield";
      var input = document.createElement("input");
      input.className = "ffield__input";
      input.type = "text";
      input.name = "sample_" + number;
      input.id = "tpl-sample-" + number;
      input.placeholder = " ";
      input.value = kept[input.name] || "";
      var label = document.createElement("label");
      label.className = "ffield__label";
      label.htmlFor = input.id;
      label.textContent = "Ejemplo para {{" + number + "}}";
      field.appendChild(input);
      field.appendChild(label);
      host.appendChild(field);
    });
    wrap.hidden = numbers.length === 0;
  }

  // Re-render the WhatsApp bubble from the form: {{n}} replaced by its
  // sample value (the literal {{n}} until one is typed), buttons the way
  // WhatsApp draws them. Everything set via textContent -- never markup.
  function syncTemplatePreview(form) {
    var preview = document.querySelector("[data-wa-preview]");
    if (!preview) return;

    var headerKind = checkedValue(form, "header_type");
    var headerText = headerKind === "text" ? form.elements.header_text.value : "";
    var mediaLabel = MEDIA_HEADER_LABELS[headerKind] || "";

    var samples = {};
    form.querySelectorAll("[data-sample-inputs] input").forEach(function (input) {
      samples[input.name] = input.value;
    });
    var body = form.elements.body.value.replace(
      TEMPLATE_VARIABLE_RE,
      function (token, number) {
        return samples["sample_" + number] || token;
      }
    );
    var footer = form.elements.footer.value;

    var buttonTexts = [];
    var buttonKind = checkedValue(form, "button_kind");
    if (buttonKind === "quick") {
      [1, 2, 3].forEach(function (i) {
        var text = form.elements["quick_reply_" + i].value.trim();
        if (text) buttonTexts.push(text);
      });
    } else if (buttonKind === "cta") {
      ["cta_url_text", "cta_phone_text"].forEach(function (name) {
        var text = form.elements[name].value.trim();
        if (text) buttonTexts.push(text);
      });
    }

    var hasContent = !!(headerText || mediaLabel || body || footer || buttonTexts.length);
    preview.querySelector("[data-wa-bubble]").hidden = !hasContent;
    preview.querySelector("[data-wa-empty]").hidden = hasContent;

    var headerEl = preview.querySelector("[data-wa-header]");
    headerEl.hidden = !headerText;
    headerEl.textContent = headerText;

    var mediaEl = preview.querySelector("[data-wa-media]");
    mediaEl.hidden = !mediaLabel;
    preview.querySelector("[data-wa-media-label]").textContent = mediaLabel;

    preview.querySelector("[data-wa-body]").textContent = body;

    var footerEl = preview.querySelector("[data-wa-footer]");
    footerEl.hidden = !footer;
    footerEl.textContent = footer;

    var buttonsEl = preview.querySelector("[data-wa-buttons]");
    buttonsEl.hidden = !buttonTexts.length;
    buttonsEl.textContent = "";
    buttonTexts.forEach(function (text) {
      var button = document.createElement("span");
      button.className = "wa-bubble__button";
      button.textContent = text;
      buttonsEl.appendChild(button);
    });
  }

  // Full sync -- on first load and after any swap that (re)renders the
  // editor. A no-op on every other screen.
  function syncTemplateEditor() {
    var form = document.querySelector("[data-plantilla-form]");
    if (!form) return;
    syncTemplateCategory(form);
    syncTemplateHeader(form);
    syncTemplateButtons(form);
    syncTemplateSamples(form);
    syncTemplateCounters(form);
    syncTemplatePreview(form);
  }

  document.addEventListener("input", function (event) {
    var form = event.target.closest && event.target.closest("[data-plantilla-form]");
    if (!form) return;
    if (event.target.matches("[data-name-input]")) syncTemplateName(event.target);
    if (event.target.matches("[data-body-input]")) syncTemplateSamples(form);
    syncTemplateCounters(form);
    syncTemplatePreview(form);
  });

  document.addEventListener("change", function (event) {
    var form = event.target.closest && event.target.closest("[data-plantilla-form]");
    if (!form) return;
    if (event.target.name === "category") syncTemplateCategory(form);
    if (event.target.name === "header_type") syncTemplateHeader(form);
    if (event.target.name === "button_kind") syncTemplateButtons(form);
    syncTemplatePreview(form);
  });

  // "+ Añadir variable": insert the next {{n}} at the cursor.
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest && event.target.closest("[data-add-variable]");
    if (!trigger) return;
    var form = trigger.closest("[data-plantilla-form]");
    var body = form && form.querySelector("[data-body-input]");
    if (!body) return;
    var numbers = bodyVariableNumbers(body.value);
    var next = numbers.length ? Math.max.apply(null, numbers) + 1 : 1;
    var token = "{{" + next + "}}";
    var start = body.selectionStart === null ? body.value.length : body.selectionStart;
    var end = body.selectionEnd === null ? start : body.selectionEnd;
    body.value = body.value.slice(0, start) + token + body.value.slice(end);
    body.focus();
    body.setSelectionRange(start + token.length, start + token.length);
    syncTemplateSamples(form);
    syncTemplateCounters(form);
    syncTemplatePreview(form);
  });

  document.addEventListener("htmx:afterSwap", function () {
    syncTemplateEditor();
  });

  // Back/forward restores a snapshot without firing afterSwap.
  document.addEventListener("htmx:historyRestore", function () {
    syncTemplateEditor();
  });

  // shell.js loads deferred, so the DOM is ready by now.
  syncTemplateEditor();

  /* -------------------------------------------------------------------------
   * Tag modal live preview: the pill mirrors the name as it is typed and the
   * swatch as it is picked.
   * ---------------------------------------------------------------------- */

  function syncTagPreview(scope) {
    var input = scope.querySelector("[data-tag-name-input]");
    var preview = scope.querySelector("[data-tag-preview]");
    if (!input || !preview) return;
    preview.textContent = input.value.trim() || "ETIQUETA";
    var color = scope.querySelector('input[name="color"]:checked');
    if (color) preview.className = "tag-pill tag-pill--" + color.value;
  }

  document.addEventListener("input", function (event) {
    if (!event.target.closest || !event.target.closest("[data-tag-name-input]")) return;
    var form = event.target.closest("form");
    if (form) syncTagPreview(form);
  });

  document.addEventListener("change", function (event) {
    if (event.target.name === "color" && event.target.closest("dialog")) {
      syncTagPreview(event.target.closest("form"));
    }
  });

  /* -------------------------------------------------------------------------
   * Dropdowns ([data-dropdown] <details>): close on outside click and on
   * Escape -- <details> only closes itself on a second summary click.
   * ---------------------------------------------------------------------- */

  function closeDropdowns(except) {
    document.querySelectorAll("details[data-dropdown][open]").forEach(function (d) {
      if (d !== except) d.removeAttribute("open");
    });
  }

  document.addEventListener("click", function (event) {
    var inside = event.target.closest("details[data-dropdown]");
    closeDropdowns(inside);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeDropdowns(null);
  });

  /* -------------------------------------------------------------------------
   * Conversation multi-select: the select-all box drives the row boxes, and
   * any change updates the Acciones dropdown's visibility (.has-selection on
   * the bulk form) and its count chip. List re-renders arrive unchecked, so
   * the state is re-derived after every #conv-list swap.
   * ---------------------------------------------------------------------- */

  function refreshSelection() {
    var checked = document.querySelectorAll(".conv-item__check:checked").length;
    var form = document.getElementById("bulk-form");
    if (form) form.classList.toggle("has-selection", checked > 0);
    var count = document.querySelector("[data-selection-count]");
    if (count) {
      count.hidden = checked === 0;
      count.textContent = checked;
    }
    var all = document.querySelector("[data-select-all]");
    if (all && checked === 0) all.checked = false;
  }

  document.addEventListener("change", function (event) {
    if (event.target.matches && event.target.matches("[data-select-all]")) {
      document.querySelectorAll(".conv-item__check").forEach(function (box) {
        box.checked = event.target.checked;
      });
      refreshSelection();
      return;
    }
    if (event.target.matches && event.target.matches(".conv-item__check")) {
      refreshSelection();
    }
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail.target;
    if (target.id === "conv-list" || target.querySelector("#conv-list")) {
      refreshSelection();
    }
  });

  /* -------------------------------------------------------------------------
   * Respuestas rápidas: the composer's plantillas picker.
   *
   * Picking an entry FILLS the input rather than sending -- the agent
   * reviews (a sample value may need replacing) and Enviar stays the only
   * send. All listeners are document-level delegation, so the picker keeps
   * working across every chat_thread re-render.
   * ---------------------------------------------------------------------- */

  function closeQuickReplies(except) {
    document.querySelectorAll("[data-quickreplies][open]").forEach(function (el) {
      if (el !== except) el.open = false;
    });
  }

  document.addEventListener("click", function (event) {
    var item = event.target.closest("[data-quick-body]");
    if (item) {
      var composer = item.closest(".composer");
      var input = composer && composer.querySelector(".composer__input");
      if (input) {
        input.value = item.dataset.quickBody;
        input.focus();
        // Cursor at the end -- ready to append, or to spot a {{n}} to fill.
        input.setSelectionRange(input.value.length, input.value.length);
      }
      closeQuickReplies();
      return;
    }
    // A click anywhere outside an open picker closes it (its own summary
    // already toggles itself -- don't fight the native behavior).
    closeQuickReplies(event.target.closest("[data-quickreplies]"));
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var open = document.querySelector("[data-quickreplies][open]");
    if (!open) return;
    open.open = false;
    var summary = open.querySelector("summary");
    if (summary) summary.focus();
  });
})();
