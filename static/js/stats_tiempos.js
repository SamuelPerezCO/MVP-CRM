/* ==========================================================================
 * Tiempos de Respuesta -- the screen's own glue, on the stats_volumen.js
 * pattern (see that file's header for the re-execution and scoping rules).
 *
 * What differs from volumen: THREE filters govern the page -- agent,
 * platform and period -- so the one fetch carries all three, and the two
 * selects trigger it on change. The chart is a bar distribution
 * (StatsChart.bar), not a line.
 * ========================================================================== */

(function () {
  "use strict";

  var root = document.querySelector("[data-tiempos-root]");
  if (!root || root.__tiemposInit) return;
  root.__tiemposInit = true;

  var payload = document.getElementById("tiempos-report");
  if (!payload) return;
  var report = JSON.parse(payload.textContent);

  var plot = root.querySelector("[data-chart]");
  var emptyNote = root.querySelector("[data-chart-empty]");
  var busy = root.querySelector("[data-busy]");
  var agentSelect = root.querySelector("[data-filter-agent]");
  var platformSelect = root.querySelector("[data-filter-platform]");
  var chart = null;

  /* --- Chart -------------------------------------------------------------- */

  function chartSpec(data) {
    return {
      categories: data.bands,
      series: data.series,
      percent: true,
      exportName: "tiempos-de-respuesta-" + data.start + "-" + data.end,
    };
  }

  function renderChart(data) {
    var hasData = data.responses > 0;
    if (emptyNote) emptyNote.hidden = hasData;
    plot.hidden = !hasData;

    if (!hasData) {
      if (chart) { chart.destroy(); chart = null; }
      return;
    }
    if (chart) chart.update(chartSpec(data));
    else chart = window.StatsChart.bar(plot, chartSpec(data));
  }

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

  // Rebuilt rather than patched: the Post-MIA column comes and goes with
  // whether the period has escalations.
  function renderTable(data) {
    var head = root.querySelector("[data-table] thead tr");
    var body = root.querySelector("[data-table-body]");
    if (!head || !body) return;

    head.textContent = "";
    ["Rango"].concat(data.series.map(function (s) { return s.label; }))
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
      row.values.forEach(function (value) {
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

  /* --- The one fetch ------------------------------------------------------ */

  var pending = null;

  function currentFilters() {
    return {
      start: report.start,
      end: report.end,
      agent: agentSelect ? agentSelect.value : "",
      platform: platformSelect ? platformSelect.value : "",
    };
  }

  function fetchReport(filters) {
    if (pending) pending.abort();
    var controller = new AbortController();
    pending = controller;
    if (busy) busy.hidden = false;

    var url = root.dataset.reportUrl +
      "?start=" + encodeURIComponent(filters.start) +
      "&end=" + encodeURIComponent(filters.end) +
      "&agent=" + encodeURIComponent(filters.agent) +
      "&platform=" + encodeURIComponent(filters.platform);

    fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(function (data) {
        pending = null;
        if (busy) busy.hidden = true;
        // The server clamps unusable values back to their defaults and says
        // so in the response; follow it rather than the inputs.
        if (startInput) startInput.value = data.start;
        if (endInput) endInput.value = data.end;
        if (agentSelect) agentSelect.value = data.agent;
        if (platformSelect) platformSelect.value = data.platform;
        apply(data);
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        pending = null;
        if (busy) busy.hidden = true;
        showError("No se pudo actualizar. Intenta de nuevo.");
        setPopOpen(true);
      });
  }

  if (agentSelect) {
    agentSelect.addEventListener("change", function () {
      fetchReport(currentFilters());
    });
  }
  if (platformSelect) {
    platformSelect.addEventListener("change", function () {
      fetchReport(currentFilters());
    });
  }

  /* --- Period picker ------------------------------------------------------ */

  var pop = root.querySelector("[data-range-pop]");
  var startInput = root.querySelector("[data-range-start]");
  var endInput = root.querySelector("[data-range-end]");
  var error = root.querySelector("[data-range-error]");

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

  root.addEventListener("click", function (event) {
    var target = event.target;

    if (target.closest("[data-range-open]")) {
      setPopOpen(pop.hidden);
      return;
    }
    if (target.closest("[data-range-cancel]")) {
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
      var filters = currentFilters();
      filters.start = start;
      filters.end = end;
      fetchReport(filters);
      return;
    }
    if (target.closest("[data-range-reset]")) {
      showError("");
      setPopOpen(false);
      var reset = currentFilters();
      reset.start = root.dataset.defaultStart;
      reset.end = root.dataset.defaultEnd;
      fetchReport(reset);
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

    if (!pop.hidden && !target.closest("[data-daterange]")) setPopOpen(false);
  });

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
