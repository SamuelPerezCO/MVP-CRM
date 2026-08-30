/* ==========================================================================
 * Volumen de Mensajes -- the screen's own glue: the period picker, the
 * re-fetch it triggers, and the "Ver tabla" toggle. The chart itself is
 * StatsChart's job (static/js/stats_chart.js).
 *
 * Loaded by the panel, so it may execute more than once per page life: every
 * listener is scoped to the current panel's root, never to document, and
 * init is guarded per panel instance. On swap-out htmx announces the cleanup
 * and the ECharts instance is disposed.
 *
 * The screen renders complete from the server -- tiles, table and the
 * report in a json_script tag. This file adds the canvas and makes the
 * period live; with it absent or still loading, everything but the chart
 * and the picker still works.
 *
 * One period governs the page, so there is exactly one place it can change
 * and exactly one fetch when it does. Every visible number is written from
 * the report the server returns (report.tiles / report.table carry their own
 * formatted strings) -- nothing is re-derived here, so the tiles, the chart
 * and the table cannot drift apart.
 * ========================================================================== */

(function () {
  "use strict";

  // A JS expando, not a data- attribute: an attribute would be serialized
  // into htmx's history snapshot and block re-init after back/forward.
  var root = document.querySelector("[data-volumen-root]");
  if (!root || root.__volumenInit) return;
  root.__volumenInit = true;

  var payload = document.getElementById("volumen-report");
  if (!payload) return;
  var report = JSON.parse(payload.textContent);

  var plot = root.querySelector("[data-chart]");
  var emptyNote = root.querySelector("[data-chart-empty]");
  var busy = root.querySelector("[data-busy]");
  var chart = null;

  /* --- Chart -------------------------------------------------------------- */

  function chartSpec(data) {
    return {
      categories: data.days,
      series: data.series,
      exportName: "volumen-de-mensajes-" + data.start + "-" + data.end,
    };
  }

  function renderChart(data) {
    var hasData = data.series.length > 0;
    if (emptyNote) emptyNote.hidden = hasData;
    plot.hidden = !hasData;

    if (!hasData) {
      // Nothing to draw. Dispose rather than leave the previous period's
      // lines sitting under an "empty period" message.
      if (chart) { chart.destroy(); chart = null; }
      return;
    }
    if (chart) chart.update(chartSpec(data));
    else chart = window.StatsChart.line(plot, chartSpec(data));
  }

  // The bundle and StatsChart are inserted by htmx as external scripts, which
  // load async -- they may land after this file. Poll briefly, and give up
  // quietly if they never arrive or the panel is swapped away: the tiles and
  // the table are already on screen either way.
  function boot(tries) {
    if (!root.isConnected) return;
    if (!window.echarts || !window.StatsChart) {
      if (tries > 0) setTimeout(function () { boot(tries - 1); }, 30);
      return;
    }
    renderChart(report);
  }
  boot(100);

  root.addEventListener("htmx:beforeCleanupElement", function (event) {
    if (event.target === root && chart) chart.destroy();
  });

  /* --- Applying a new report --------------------------------------------- */

  function setText(selector, text) {
    var el = root.querySelector(selector);
    if (el) el.textContent = text;
  }

  function renderTiles(data) {
    data.tiles.forEach(function (tile) {
      setText('[data-kpi-value="' + tile.key + '"]', tile.value);
      setText('[data-kpi-note="' + tile.key + '"]', tile.note);
    });
  }

  // Rebuilt rather than patched: the column count changes with the number of
  // channels that have data, so the header has to be rewritten too.
  function renderTable(data) {
    var head = root.querySelector("[data-table] thead tr");
    var body = root.querySelector("[data-table-body]");
    if (!head || !body) return;

    head.textContent = "";
    ["Fecha"].concat(data.series.map(function (s) { return s.label; }), ["Total"])
      .forEach(function (label) {
        var th = document.createElement("th");
        th.scope = "col";
        th.textContent = label;
        head.appendChild(th);
      });

    body.textContent = "";
    data.table.forEach(function (row) {
      var tr = document.createElement("tr");
      var rowHead = document.createElement("th");
      rowHead.scope = "row";
      rowHead.textContent = row.label;
      tr.appendChild(rowHead);
      row.values.concat([row.total]).forEach(function (value) {
        var td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  function apply(data) {
    report = data;
    setText("[data-range-label]", data.range_label);
    setText("[data-table-range]", data.range_label);
    renderTiles(data);
    renderTable(data);
    renderChart(data);
  }

  /* --- Period picker ------------------------------------------------------ */

  var pop = root.querySelector("[data-range-pop]");
  var startInput = root.querySelector("[data-range-start]");
  var endInput = root.querySelector("[data-range-end]");
  var error = root.querySelector("[data-range-error]");
  var pending = null;

  function openers() {
    return root.querySelectorAll("[data-range-open][aria-expanded]");
  }

  function setPopOpen(open) {
    pop.hidden = !open;
    openers().forEach(function (el) {
      el.setAttribute("aria-expanded", open ? "true" : "false");
    });
    if (open && startInput) startInput.focus();
  }

  function showError(message) {
    if (!error) return;
    error.textContent = message;
    error.hidden = !message;
  }

  function fetchRange(start, end) {
    // Drop the previous answer if the picker moves again while it is in
    // flight -- otherwise a slow first request can land after a fast second
    // one and put the wrong period on screen.
    if (pending) pending.abort();
    var controller = new AbortController();
    pending = controller;
    if (busy) busy.hidden = false;

    var url = root.dataset.reportUrl + "?start=" + start + "&end=" + end;
    fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(function (data) {
        pending = null;
        if (busy) busy.hidden = true;
        // The server clamps an unusable range back to the default and says
        // so in the response; follow it rather than the inputs.
        if (startInput) startInput.value = data.start;
        if (endInput) endInput.value = data.end;
        apply(data);
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        pending = null;
        if (busy) busy.hidden = true;
        showError("No se pudo actualizar el período. Intenta de nuevo.");
        setPopOpen(true);
      });
  }

  root.addEventListener("click", function (event) {
    var target = event.target;

    if (target.closest("[data-range-open]")) {
      setPopOpen(pop.hidden);
      return;
    }
    if (target.closest("[data-range-cancel]")) {
      // Discard edits: put the applied period back in the inputs.
      if (startInput) startInput.value = report.start;
      if (endInput) endInput.value = report.end;
      showError("");
      setPopOpen(false);
      return;
    }
    if (target.closest("[data-range-apply]")) {
      var start = startInput.value;
      var end = endInput.value;
      if (!start || !end) { showError("Elige las dos fechas."); return; }
      if (end < start) { showError("La fecha final no puede ser anterior a la inicial."); return; }
      showError("");
      setPopOpen(false);
      fetchRange(start, end);
      return;
    }
    // The "x": the period is never empty -- there is always a window being
    // shown -- so clearing means back to the default last 30 days.
    if (target.closest("[data-range-reset]")) {
      showError("");
      setPopOpen(false);
      fetchRange(root.dataset.defaultStart, root.dataset.defaultEnd);
      return;
    }

    var toggle = target.closest("[data-table-toggle]");
    if (toggle) {
      var table = root.querySelector("[data-table]");
      var open = table.hidden;
      table.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Ocultar tabla" : "Ver tabla";
      return;
    }

    // A click anywhere else inside the screen closes the popover, unless it
    // landed in the popover itself.
    if (!pop.hidden && !target.closest("[data-daterange]")) setPopOpen(false);
  });

  // Clicks outside the panel entirely, and Escape, also close it. Both are
  // document-level by necessity; they no-op once the panel is gone.
  document.addEventListener("click", function (event) {
    if (!root.isConnected || pop.hidden) return;
    if (!event.target.closest || !event.target.closest("[data-daterange]")) setPopOpen(false);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !root.isConnected || pop.hidden) return;
    setPopOpen(false);
    var field = root.querySelector("[data-range-open]");
    if (field) field.focus();
  });
})();
