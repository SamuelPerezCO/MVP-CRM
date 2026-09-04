/* ==========================================================================
 * Mi calendario -- boots FullCalendar and wires the sidebar (mini month
 * picker, preferences), the toolbar, the event modal and drag/resize
 * persistence to it. Loaded by the panel itself (only this screen pays for
 * the vendored bundle), so it may execute more than once per page life:
 * every listener is scoped to the current panel's elements, never to
 * document/window, and init is guarded per panel instance. On swap-out
 * htmx announces the cleanup and the FullCalendar instance is destroyed.
 *
 * Times: FullCalendar runs with timeZone "America/Bogota" and no timezone
 * plugin, so its Date objects are UTC-COERCED WALL CLOCKS -- formatting
 * them with toISOString() yields Bogotá wall-clock strings, which is
 * exactly what the server's parse_client_dt expects. Nothing here may use
 * getHours()/local formatting on FullCalendar dates.
 * ========================================================================== */

(function () {
  "use strict";

  // The guard is a JS expando on purpose: a data- attribute would be
  // serialized into htmx's history snapshot and block re-init after
  // back/forward restores the panel.
  var root = document.querySelector("[data-calendar-root]");
  if (!root || root.__calInit) return;
  root.__calInit = true;

  // Copied from @fullcalendar/core 6.1.19 locales/es -- embedded instead of
  // loaded as a second <script> because HTMX-inserted scripts execute async
  // and the locale file needs the FullCalendar global already present.
  // Re-copy from the package when upgrading the vendored bundle.
  var ES_LOCALE = {
    code: "es",
    week: { dow: 1, doy: 4 },
    buttonText: { prev: "Ant", next: "Sig", today: "Hoy", year: "Año", month: "Mes", week: "Semana", day: "Día", list: "Agenda" },
    weekText: "Sm",
    weekTextLong: "Semana",
    allDayText: "Todo el día",
    moreLinkText: "más",
    noEventsText: "No hay eventos para mostrar",
  };

  var MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
  var MINI_DAY_LETTERS = ["D", "L", "M", "M", "J", "V", "S"]; // Sunday start
  var DAY_MS = 24 * 60 * 60 * 1000;

  // The screen may be swapped in by HTMX, whose inserted external scripts
  // load async -- the vendor bundle may land after this file. Poll briefly,
  // give up if the bundle never arrives or the panel is swapped away.
  function boot(tries) {
    if (!root.isConnected) return;
    if (!window.FullCalendar) {
      if (tries > 0) setTimeout(function () { boot(tries - 1); }, 30);
      return;
    }
    init();
  }
  boot(100);

  /* --- Small helpers ----------------------------------------------------- */

  function bogotaNowStr() {
    // "sv-SE" formats as YYYY-MM-DD HH:mm:ss; with the timeZone option this
    // is the Bogotá wall clock whatever the browser's own zone is.
    return new Date()
      .toLocaleString("sv-SE", { timeZone: "America/Bogota" })
      .replace(" ", "T");
  }

  function bogotaTodayStr() {
    return bogotaNowStr().slice(0, 10);
  }

  // A coerced FullCalendar Date -> "YYYY-MM-DDTHH:mm:ss" wall clock.
  function wallClock(date) {
    return date.toISOString().slice(0, 19);
  }

  function init() {
    var csrf = root.dataset.csrf;
    var eventsUrl = root.dataset.eventsUrl;
    var createUrl = root.dataset.createUrl;
    var prefsUrl = root.dataset.prefsUrl;

    function post(url, fields) {
      var body = new FormData();
      Object.keys(fields).forEach(function (key) { body.append(key, fields[key]); });
      return fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": csrf },
        body: body,
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) return Promise.reject(data);
          return data;
        });
      });
    }

    /* --- Toast (persistent until dismissed) -------------------------------- */

    var toastEl = root.querySelector("[data-cal-toast]");
    var toastMsg = root.querySelector("[data-cal-toast-msg]");

    function toast(message) {
      toastMsg.textContent = message;
      toastEl.hidden = false;
    }

    root.querySelector("[data-cal-toast-close]").addEventListener("click", function () {
      toastEl.hidden = true;
    });

    /* --- FullCalendar ---------------------------------------------------- */

    var grid = root.querySelector("[data-calendar-grid]");
    var titleEl = root.querySelector("[data-cal-title]");
    var titleHeading = titleEl.closest("h1");
    var viewSelect = root.querySelector("[data-cal-view]");

    // Mini-picker state must exist before the calendar renders: datesSet
    // fires during the initial render() and syncs it.
    var miniTitle = root.querySelector("[data-mini-title]");
    var miniGrid = root.querySelector("[data-mini-grid]");
    var today = bogotaTodayStr();
    var miniState = {
      year: parseInt(today.slice(0, 4), 10),
      month: parseInt(today.slice(5, 7), 10) - 1,
      selected: today,
    };

    var calendar = new FullCalendar.Calendar(grid, {
      locale: ES_LOCALE,
      timeZone: "America/Bogota",
      initialView: "timeGridWeek",
      headerToolbar: false, // the toolbar above the grid is our own
      height: "100%",
      weekends: root.dataset.weekends === "1",
      slotDuration: root.dataset.slot,
      slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
      // Nobody schedules at 3am; land the viewport on business hours.
      scrollTime: "07:00:00",
      nowIndicator: true,
      now: bogotaNowStr,
      editable: true,
      selectable: true,
      selectMirror: true,
      dayHeaderFormat: { weekday: "short", day: "numeric" },
      titleFormat: { day: "numeric", month: "short", year: "numeric" },
      dayMaxEventRows: true, // month view: "+n más" instead of overflow

      events: function (info, success, failure) {
        fetch(eventsUrl + "?start=" + encodeURIComponent(info.startStr) +
              "&end=" + encodeURIComponent(info.endStr))
          .then(function (response) {
            if (!response.ok) throw new Error();
            return response.json();
          })
          .then(success)
          .catch(function () {
            failure();
            toast("No se pudieron cargar los eventos.");
          });
      },

      datesSet: function (arg) {
        titleEl.textContent = arg.view.title;
        viewSelect.value = arg.view.type;
        miniState.selected = calendar.getDate().toISOString().slice(0, 10);
        var selected = new Date(calendar.getDate());
        miniState.year = selected.getUTCFullYear();
        miniState.month = selected.getUTCMonth();
        renderMini();
      },

      select: function (info) {
        var prefill = {
          date: info.startStr.slice(0, 10),
          allDay: info.allDay,
        };
        if (info.allDay) {
          // FC's all-day end is exclusive; the modal's Hasta is inclusive.
          prefill.endDate = wallClock(new Date(info.end.getTime() - DAY_MS)).slice(0, 10);
        } else {
          prefill.start = wallClock(info.start).slice(11, 16);
          prefill.end = wallClock(info.end).slice(11, 16);
        }
        openDialog(prefill);
        calendar.unselect();
      },

      eventClick: function (info) {
        openDialog({ event: info.event });
      },

      // The chip shows WHO the event is with, not just what it is called:
      // serialize_event ships contactName for exactly this, and a calendar
      // inside a CRM that hid the client on every event would be missing
      // its point. Time-grid chips keep their time line above the title.
      eventContent: function (arg) {
        var wrap = document.createElement("div");
        wrap.className = "cal-event__body";
        if (arg.timeText) {
          var time = document.createElement("div");
          time.className = "cal-event__time";
          time.textContent = arg.timeText;
          wrap.appendChild(time);
        }
        var title = document.createElement("div");
        title.className = "cal-event__title";
        title.textContent = arg.event.title;
        wrap.appendChild(title);
        var client = arg.event.extendedProps.contactName;
        if (client) {
          var who = document.createElement("div");
          who.className = "cal-event__client";
          who.textContent = client;
          wrap.appendChild(who);
        }
        return { domNodes: [wrap] };
      },

      // Hovering a cramped chip reveals the full line either way.
      eventDidMount: function (arg) {
        var client = arg.event.extendedProps.contactName;
        arg.el.title = client ? arg.event.title + " · " + client : arg.event.title;
      },

      eventDrop: persistMove,
      eventResize: persistMove,
    });
    calendar.render();

    // Swap-out teardown: htmx announces each element it discards; destroying
    // releases FullCalendar's window resize listener and its timers.
    root.addEventListener("htmx:beforeCleanupElement", function (event) {
      if (event.target === root) calendar.destroy();
    });

    // FC computes the initial scrollTime before the flex layout settles the
    // grid's final height, which strands the viewport around 03:00; re-apply
    // once the first frame has painted.
    requestAnimationFrame(function () {
      calendar.scrollToTime("07:00:00");
    });

    function persistMove(info) {
      var event = info.event;
      var end = event.end;
      if (!end) {
        // A lane-crossing drag drops the end (allDayMaintainDuration is
        // false by default): synthesize one -- a day for the all-day lane,
        // the previous duration (min. one hour) for the timed grid.
        var duration = event.allDay
          ? DAY_MS
          : Math.max(
              info.oldEvent.end ? info.oldEvent.end - info.oldEvent.start : 0,
              60 * 60 * 1000
            );
        end = new Date(event.start.getTime() + duration);
      }
      post(eventsUrl + event.id + "/mover/", {
        start: wallClock(event.start),
        end: wallClock(end),
        all_day: event.allDay ? "1" : "0",
      })
        .then(function () {
          // The server snaps all-day ranges to midnights; refetch so the
          // grid shows the stored truth, not the optimistic guess.
          calendar.refetchEvents();
        })
        .catch(function () {
          info.revert();
          toast("No se pudo guardar el cambio. Se restauró el evento.");
        });
    }

    /* --- Toolbar ----------------------------------------------------------- */

    root.querySelector("[data-cal-today]").addEventListener("click", function () {
      calendar.today();
    });
    root.querySelector("[data-cal-prev]").addEventListener("click", function () {
      calendar.prev();
    });
    root.querySelector("[data-cal-next]").addEventListener("click", function () {
      calendar.next();
    });
    viewSelect.addEventListener("change", function () {
      calendar.changeView(viewSelect.value);
    });

    /* --- Preferences ------------------------------------------------------- */

    var weekendsCheck = root.querySelector("[data-pref-weekends]");
    var slotSelect = root.querySelector("[data-pref-slot]");

    function savePrefs() {
      post(prefsUrl, {
        weekends: weekendsCheck.checked ? "1" : "0",
        slot: slotSelect.value,
      }).catch(function () {
        toast("No se pudieron guardar las preferencias.");
      });
    }

    weekendsCheck.addEventListener("change", function () {
      calendar.setOption("weekends", weekendsCheck.checked);
      savePrefs();
    });
    slotSelect.addEventListener("change", function () {
      calendar.setOption("slotDuration", slotSelect.value);
      savePrefs();
    });

    /* --- Mini month picker -------------------------------------------------- */

    function pad(n) { return (n < 10 ? "0" : "") + n; }

    function dateKey(year, month, day) {
      return year + "-" + pad(month + 1) + "-" + pad(day);
    }

    function renderMini() {
      var hadFocus = miniGrid.contains(document.activeElement);
      miniTitle.textContent = MONTHS[miniState.month] + " de " + miniState.year;
      miniGrid.textContent = "";

      MINI_DAY_LETTERS.forEach(function (letter) {
        var head = document.createElement("span");
        head.className = "mini-cal__dow";
        // Single letters (two bare "M"s) are noise to a screen reader; the
        // day buttons below carry full-date labels.
        head.setAttribute("aria-hidden", "true");
        head.textContent = letter;
        miniGrid.appendChild(head);
      });

      // Sunday-start grid: cells before the 1st come from the prior month.
      var first = new Date(Date.UTC(miniState.year, miniState.month, 1));
      var lead = first.getUTCDay(); // 0 = Sunday
      var cursor = new Date(Date.UTC(miniState.year, miniState.month, 1 - lead));
      for (var i = 0; i < 42; i++) {
        var y = cursor.getUTCFullYear();
        var m = cursor.getUTCMonth();
        var d = cursor.getUTCDate();
        var key = dateKey(y, m, d);

        var cell = document.createElement("button");
        cell.type = "button";
        cell.className = "mini-cal__day";
        cell.tabIndex = -1; // roving tabindex; one stop for the whole grid
        if (m !== miniState.month) cell.classList.add("mini-cal__day--outside");
        if (key === miniState.selected) {
          cell.classList.add("mini-cal__day--selected");
          cell.setAttribute("aria-pressed", "true");
        }
        if (key === today) {
          cell.classList.add("mini-cal__day--today");
          cell.setAttribute("aria-current", "date");
        }
        cell.textContent = d;
        cell.dataset.date = key;
        cell.setAttribute("aria-label", d + " de " + MONTHS[m] + " de " + y);
        miniGrid.appendChild(cell);

        cursor.setUTCDate(d + 1);
      }

      var focusTarget =
        miniGrid.querySelector('[data-date="' + miniState.selected + '"]') ||
        miniGrid.querySelector(".mini-cal__day:not(.mini-cal__day--outside)");
      if (focusTarget) {
        focusTarget.tabIndex = 0;
        // The rebuild destroyed the focused cell mid-interaction; put the
        // keyboard user back on the grid instead of dropping them to body.
        if (hadFocus) focusTarget.focus();
      }
    }

    root.querySelector("[data-mini-prev]").addEventListener("click", function () {
      miniState.month -= 1;
      if (miniState.month < 0) { miniState.month = 11; miniState.year -= 1; }
      renderMini();
    });
    root.querySelector("[data-mini-next]").addEventListener("click", function () {
      miniState.month += 1;
      if (miniState.month > 11) { miniState.month = 0; miniState.year += 1; }
      renderMini();
    });
    miniGrid.addEventListener("click", function (event) {
      var day = event.target.closest(".mini-cal__day");
      if (!day) return;
      miniState.selected = day.dataset.date;
      calendar.gotoDate(day.dataset.date); // datesSet re-renders the mini
    });
    // Arrow keys walk the grid (roving tabindex); Enter/Space are native.
    miniGrid.addEventListener("keydown", function (event) {
      var moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      if (!(event.key in moves)) return;
      var days = Array.prototype.slice.call(
        miniGrid.querySelectorAll(".mini-cal__day")
      );
      var index = days.indexOf(document.activeElement);
      if (index === -1) return;
      event.preventDefault();
      var next = days[index + moves[event.key]];
      if (!next) return; // clamp at the rendered 6 weeks
      days[index].tabIndex = -1;
      next.tabIndex = 0;
      next.focus();
    });
    renderMini();

    /* --- Event modal --------------------------------------------------------- */

    var dialog = root.querySelector("#event-dialog");
    var form = dialog.querySelector("[data-event-form]");
    var dialogTitle = dialog.querySelector("[data-event-dialog-title]");
    var submitButton = dialog.querySelector("[data-event-submit]");
    var deleteButton = dialog.querySelector("[data-event-delete]");
    var errorsEl = dialog.querySelector("[data-event-errors]");
    var allDayCheck = form.querySelector("[data-all-day]");
    var endDateField = form.querySelector("[data-end-date-field]");

    //: Server error key -> the form controls it concerns.
    var ERROR_FIELDS = {
      title: ["title"],
      event_type: ["event_type"],
      when: ["date", "start_time", "end_time", "end_date"],
      contact: ["contact"],
      assigned_to: ["assigned_to"],
      reminder: ["reminder"],
    };

    function clearFormErrors() {
      errorsEl.hidden = true;
      errorsEl.textContent = "";
      form.querySelectorAll(".ffield--error").forEach(function (field) {
        field.classList.remove("ffield--error");
      });
      form.querySelectorAll('[aria-describedby="event-dialog-errors"]').forEach(
        function (input) {
          input.removeAttribute("aria-invalid");
          input.removeAttribute("aria-describedby");
        }
      );
    }

    function showFormErrors(errors) {
      var messages = [];
      var firstInput = null;
      Object.keys(errors).forEach(function (key) {
        messages.push(errors[key]);
        (ERROR_FIELDS[key] || []).forEach(function (name) {
          var input = form.elements[name];
          if (!input) return;
          var field = input.closest(".ffield");
          if (field) field.classList.add("ffield--error");
          input.setAttribute("aria-invalid", "true");
          if (!input.getAttribute("aria-describedby")) {
            input.setAttribute("aria-describedby", "event-dialog-errors");
          }
          if (!firstInput && !input.disabled && !input.closest("[hidden]")) {
            firstInput = input;
          }
        });
      });
      errorsEl.textContent = messages.length
        ? messages.join(" ")
        : "No se pudo guardar el evento.";
      errorsEl.hidden = false;
      if (firstInput) firstInput.focus();
    }

    function syncAllDay() {
      form.querySelectorAll("[data-time-field] input").forEach(function (input) {
        input.disabled = allDayCheck.checked;
      });
      endDateField.hidden = !allDayCheck.checked;
    }
    allDayCheck.addEventListener("change", syncAllDay);

    // The modal closed after a grid click leaves focus on a rebuilt (dead)
    // element; land it on the range heading instead of <body>.
    function recoverFocus() {
      requestAnimationFrame(function () {
        if (document.activeElement === document.body) {
          titleHeading.setAttribute("tabindex", "-1");
          titleHeading.focus();
        }
      });
    }

    // ``prefill``: {} for a blank create, {date, start/end or endDate,
    // allDay} from a grid selection, or {event} for edit mode.
    function openDialog(prefill) {
      form.reset();
      clearFormErrors();

      var event = prefill.event || null;
      dialogTitle.textContent = event ? "Editar evento" : "Crear evento";
      submitButton.textContent = event ? "Guardar cambios" : "Crear evento";
      deleteButton.hidden = !event;

      if (event) {
        form.elements.event_id.value = event.id;
        form.elements.title.value = event.title;
        form.elements.date.value = wallClock(event.start).slice(0, 10);
        form.elements.all_day.checked = event.allDay;
        if (event.allDay) {
          // Stored end is exclusive; Hasta shows the inclusive last day.
          var lastDay = new Date(
            (event.end ? event.end.getTime() : event.start.getTime() + DAY_MS) - DAY_MS
          );
          form.elements.end_date.value = wallClock(lastDay).slice(0, 10);
        } else {
          form.elements.start_time.value = wallClock(event.start).slice(11, 16);
          form.elements.end_time.value = wallClock(event.end || event.start).slice(11, 16);
        }
        form.elements.event_type.value = event.extendedProps.eventType;
        setSelectValue(
          form.elements.contact,
          event.extendedProps.contactId,
          event.extendedProps.contactName
        );
        setSelectValue(form.elements.assigned_to, event.extendedProps.assignedToId, null);
        form.elements.reminder.value =
          event.extendedProps.reminder === "" ? "" : String(event.extendedProps.reminder);
        if (form.elements.reminder.value !== String(event.extendedProps.reminder) &&
            event.extendedProps.reminder !== "") {
          // Off-menu stored value (legacy data): surface it rather than
          // silently dropping it on save.
          setSelectValue(
            form.elements.reminder,
            event.extendedProps.reminder,
            event.extendedProps.reminder + " minutos antes"
          );
        }
      } else {
        form.elements.event_id.value = "";
        form.elements.date.value = prefill.date || bogotaTodayStr();
        form.elements.start_time.value = prefill.start || "09:00";
        form.elements.end_time.value = prefill.end || "10:00";
        form.elements.all_day.checked = !!prefill.allDay;
        form.elements.end_date.value = prefill.endDate || form.elements.date.value;
      }
      filterContacts("");
      syncAllDay();
      if (!dialog.open) dialog.showModal();
    }

    // A stored association whose value is missing from the menu (archived
    // contact, deactivated user) must not be silently cleared on save:
    // append it so it round-trips.
    function setSelectValue(select, value, label) {
      var wanted = value === "" || value == null ? "" : String(value);
      select.value = wanted;
      if (wanted !== "" && select.value !== wanted) {
        var option = document.createElement("option");
        option.value = wanted;
        option.textContent = label || wanted;
        select.appendChild(option);
        select.value = wanted;
        if (select === form.elements.contact) {
          allContacts.push({ value: wanted, label: option.textContent });
        }
      }
    }

    // "Crear +" opens blank. This direct listener is the only opener (the
    // button carries no data-dialog-open), so pre-init the button is inert
    // instead of opening a form that cannot submit yet.
    root.querySelector("[data-event-new]").addEventListener("click", function () {
      openDialog({});
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      clearFormErrors();
      var id = form.elements.event_id.value;
      var url = id ? eventsUrl + id + "/editar/" : createUrl;
      var fields = {
        title: form.elements.title.value,
        date: form.elements.date.value,
        start_time: form.elements.start_time.value,
        end_time: form.elements.end_time.value,
        end_date: form.elements.end_date.value,
        all_day: allDayCheck.checked ? "1" : "0",
        event_type: form.elements.event_type.value,
        contact: form.elements.contact.value,
        assigned_to: form.elements.assigned_to.value,
        description: form.elements.description.value,
        reminder: form.elements.reminder.value,
      };
      post(url, fields)
        .then(function () {
          dialog.close();
          calendar.refetchEvents();
          recoverFocus();
        })
        .catch(function (data) {
          showFormErrors((data && data.errors) || {});
        });
    });

    deleteButton.addEventListener("click", function () {
      var id = form.elements.event_id.value;
      if (!id) return;
      post(eventsUrl + id + "/eliminar/", {})
        .then(function () {
          dialog.close();
          calendar.refetchEvents();
          recoverFocus();
        })
        .catch(function () {
          errorsEl.textContent = "No se pudo eliminar el evento.";
          errorsEl.hidden = false;
        });
    });

    /* --- Contact picker filter ---------------------------------------------- */

    var contactFilter = form.querySelector("[data-contact-filter]");
    var contactSelect = form.elements.contact;
    var contactStatus = dialog.querySelector("[data-contact-filter-status]");
    // Snapshot once; filtering REBUILDS the option list because Safari's
    // native picker ignores hidden/display on <option>.
    var allContacts = Array.prototype.slice
      .call(contactSelect.options, 1)
      .map(function (option) {
        return { value: option.value, label: option.textContent.trim() };
      });

    function filterContacts(term) {
      var query = term.trim().toLowerCase();
      var selected = contactSelect.value;
      var shown = 0;
      while (contactSelect.options.length > 1) contactSelect.remove(1);
      allContacts.forEach(function (contact) {
        var matches =
          query === "" || contact.label.toLowerCase().indexOf(query) !== -1;
        // The current selection stays listed even when filtered out, so a
        // narrowing search never silently drops it.
        if (!matches && contact.value !== selected) return;
        var option = document.createElement("option");
        option.value = contact.value;
        option.textContent = contact.label;
        contactSelect.appendChild(option);
        if (matches) shown += 1;
      });
      contactSelect.value = selected;
      contactStatus.textContent =
        query === ""
          ? ""
          : shown === 0
            ? "Sin resultados"
            : shown + (shown === 1 ? " cliente" : " clientes");
    }
    contactFilter.addEventListener("input", function () {
      filterContacts(contactFilter.value);
    });
  }
})();
