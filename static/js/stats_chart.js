/* ==========================================================================
 * StatsChart -- the Estadísticas screens' shared ECharts wrapper.
 *
 * A library, not a screen: it auto-inits nothing and touches no element it
 * is not handed. Each stat detail screen loads it alongside the vendored
 * ECharts bundle and calls StatsChart.line(el, spec); the other three
 * Mensajería cards are meant to render on this same function rather than
 * hand-rolling an option object each.
 *
 * The house rules it enforces, so no caller can quietly break them:
 *
 *   ONE Y-AXIS. There is no option to add a second. Two scales stacked
 *   behind one plot make the crossing point of two lines mean nothing, and
 *   readers cannot tell which line belongs to which edge. A measure of a
 *   different magnitude gets its own chart.
 *
 *   COLOR IS NEVER THE ONLY CHANNEL. Every series is identified by a legend
 *   swatch and (on the page) by the "Ver tabla" table. Series *text* --
 *   legend labels, tooltip rows, axis ticks, the end-of-line label -- is
 *   drawn in ink, never in the series color: the swatch carries identity,
 *   and #E4405F on white is a 3.9:1 label.
 *
 *   COLORS COME FROM THE SERIES, BY KEY. Each series carries its own
 *   {light, dark} pair from the server (core.estadisticas_volumen.CHANNELS).
 *   Nothing here indexes into a palette array, so a series with no data
 *   dropping out cannot repaint the ones that remain, and dark mode uses
 *   its own validated steps rather than a lightened light value.
 *
 *   EVERY POINT IS READABLE ON HOVER. tooltip.trigger "axis" + a crosshair
 *   is mandatory, not decorative: without it, values have to be eyeballed
 *   off the axis. Only the final point of each line is direct-labelled, and
 *   only when the container is wide enough to hold the label.
 * ========================================================================== */

(function () {
  "use strict";

  /* Chart ink. Mirrors the CSS tokens (sidebar.css :root and the dark block
     in stats-detail.css) -- canvas can't read a custom property, so the
     values are duplicated here and must be changed in both places. */
  var THEME = {
    light: {
      ink: "#12263f",
      muted: "#65768d",
      grid: "#eef2f7",
      axis: "#e9edf2",
      surface: "#ffffff",
      border: "#e9edf2",
    },
    dark: {
      ink: "#e6edf6",
      muted: "#9fb0c6",
      grid: "#24303f",
      axis: "#2b3849",
      surface: "#16202c",
      border: "#2b3849",
    },
  };

  var MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic"];

  // Matches core.estadisticas_volumen.format_number -- es-CO groups with ".".
  var NUMBER_FORMAT = new Intl.NumberFormat("es-CO");

  // Below this the end-of-line labels have nowhere to sit without
  // overlapping the plot, so they are dropped and the legend carries
  // identity alone.
  var END_LABEL_MIN_WIDTH = 560;

  /**
   * Whether the chart is sitting on a dark surface.
   *
   * Deliberately measured, not read off prefers-color-scheme: the app is
   * light-only today, and a chart that went dark because the *operating
   * system* is dark would draw a dark canvas inside a white card. Reading
   * the surface instead means the dark steps switch on exactly when the
   * surrounding UI does -- the day this app grows a real dark theme, this
   * file needs no edit.
   *
   * Walks up for the first background that isn't transparent, since the
   * plot element itself usually has none.
   */
  function onDarkSurface(el) {
    for (var node = el; node && node.nodeType === 1; node = node.parentElement) {
      var match = getComputedStyle(node).backgroundColor
        .match(/^rgba?\(([^)]+)\)/);
      if (!match) continue;
      var parts = match[1].split(",").map(parseFloat);
      var alpha = parts.length > 3 ? parts[3] : 1;
      if (alpha < 0.1) continue;             // see-through: keep climbing
      // Rec.709 luma -- ample to tell a dark surface from a light one.
      var luma = (0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]) / 255;
      return luma < 0.5;
    }
    return false;
  }

  /** "2026-08-28" -> "28 ago". Parsed by hand: new Date("YYYY-MM-DD") is
      UTC midnight, which renders as the previous day west of Greenwich. */
  function shortDate(iso) {
    var parts = String(iso).split("-");
    if (parts.length !== 3) return iso;
    return parseInt(parts[2], 10) + " " + MONTHS_SHORT[parseInt(parts[1], 10) - 1];
  }

  function formatNumber(value) {
    return NUMBER_FORMAT.format(value);
  }

  /** Escape for the tooltip, which ECharts renders as HTML. */
  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  /**
   * Build the option object for a multi-series daily line chart.
   *
   * spec: {
   *   categories: ["2026-07-30", ...],       // one per x position
   *   series: [{ key, label, light, dark, values: [] }, ...],
   *   unit:   "mensajes"                      // tooltip/aria wording
   * }
   */
  function lineOption(spec, palette, width) {
    var showEndLabel = width >= END_LABEL_MIN_WIDTH;

    var series = spec.series.map(function (entry) {
      var color = palette.dark ? entry.dark : entry.light;
      return {
        name: entry.label,
        type: "line",
        smooth: true,
        smoothMonotone: "x",       // no invented dips between points
        symbol: "circle",
        symbolSize: 8,             // >= 8px: a hit target, not a dot
        showSymbol: spec.categories.length <= 45,
        lineStyle: { width: 2, color: color },
        itemStyle: { color: color },
        emphasis: { focus: "series", scale: 1.4 },
        data: entry.values,
        // Direct label on the final point only, in ink -- the swatch beside
        // it is what says which series this is. shiftY keeps two lines that
        // finish close together from printing on top of each other.
        endLabel: showEndLabel ? {
          show: true,
          color: palette.theme.ink,
          fontSize: 11,
          fontWeight: 600,
          distance: 6,
          formatter: function (params) { return formatNumber(params.value); },
        } : { show: false },
        labelLayout: { moveOverlap: "shiftY" },
      };
    });

    return {
      // Canvas text should match the page, not ECharts' default sans.
      textStyle: {
        fontFamily: '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
        color: palette.theme.ink,
      },
      animationDuration: 420,
      color: series.map(function (s) { return s.lineStyle.color; }),

      // Above the plot, per the reference. Labels in ink; the swatch is the
      // only colored thing.
      legend: {
        top: 0,
        left: 0,
        icon: "roundRect",
        itemWidth: 11,
        itemHeight: 11,
        itemGap: 18,
        textStyle: { color: palette.theme.ink, fontSize: 12.5 },
        inactiveColor: palette.theme.muted,
      },

      tooltip: {
        // Not optional: one hover reads all series at the hovered date.
        trigger: "axis",
        axisPointer: {
          type: "cross",
          // Ink field, surface-colored text: ECharts' default label text is
          // white, which vanishes on the near-white ink of a dark surface.
          label: {
            backgroundColor: palette.theme.ink,
            color: palette.theme.surface,
          },
          crossStyle: { color: palette.theme.muted },
          lineStyle: { color: palette.theme.muted },
        },
        backgroundColor: palette.theme.surface,
        borderColor: palette.theme.border,
        borderWidth: 1,
        padding: [8, 11],
        extraCssText: "box-shadow: 0 6px 20px rgb(18 38 63 / 12%);",
        textStyle: { color: palette.theme.ink, fontSize: 12.5 },
        formatter: function (rows) {
          if (!rows.length) return "";
          var head =
            '<div style="font-weight:700;margin-bottom:4px">' +
            escapeHtml(shortDate(rows[0].axisValue)) + "</div>";
          return head + rows.map(function (row) {
            return (
              '<div style="display:flex;align-items:center;gap:7px">' +
              row.marker +
              '<span style="flex:1">' + escapeHtml(row.seriesName) + "</span>" +
              '<span style="font-weight:700">' +
              escapeHtml(formatNumber(row.value == null ? 0 : row.value)) +
              "</span></div>"
            );
          }).join("");
        },
      },

      // right leaves room for the end labels; bottom clears the axis
      // labels and the toolbox row beneath them.
      grid: {
        top: 42,
        left: 6,
        right: showEndLabel ? 62 : 12,
        bottom: 42,
        containLabel: true,
      },

      xAxis: {
        type: "category",
        boundaryGap: false,
        data: spec.categories,
        axisLine: { lineStyle: { color: palette.theme.axis } },
        axisTick: { show: false },
        axisLabel: {
          color: palette.theme.muted,
          fontSize: 11.5,
          hideOverlap: true,       // thins ticks instead of stacking them
          formatter: shortDate,
        },
        // The crosshair's own label, which otherwise prints the raw
        // category ("2026-08-12") right under a tooltip saying "12 ago".
        axisPointer: {
          label: { formatter: function (params) { return shortDate(params.value); } },
        },
      },

      // Deliberately singular. See the header note.
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: palette.theme.muted,
          fontSize: 11.5,
          formatter: formatNumber,
        },
        splitLine: { lineStyle: { color: palette.theme.grid } },
        // Without this the crosshair reads "458.77" -- a decimal, which
        // under es-CO grouping looks like forty-five thousand.
        axisPointer: {
          label: {
            formatter: function (params) {
              return formatNumber(Math.round(params.value));
            },
          },
        },
      },

      // The reference's bottom-right cluster: zoom, restore, export.
      toolbox: {
        right: 8,
        bottom: 0,
        itemSize: 14,
        itemGap: 10,
        iconStyle: { borderColor: palette.theme.muted },
        emphasis: { iconStyle: { borderColor: palette.theme.ink } },
        feature: {
          dataZoom: {
            yAxisIndex: "none",   // zooming the value axis would lie
            title: { zoom: "Acercar", back: "Deshacer zoom" },
          },
          restore: { title: "Restablecer" },
          saveAsImage: {
            title: "Descargar imagen",
            name: spec.exportName || "grafico",
            backgroundColor: palette.theme.surface,
          },
        },
      },

      series: series,
    };
  }

  /**
   * Build the option object for a categorical bar chart -- the distribution
   * charts' shape: plain-string categories (time bands, not dates) and
   * percentage values on the one y-axis.
   *
   * spec: {
   *   categories: ["< 5 min", ...],
   *   series: [{ key, label, light, dark, values: [] }, ...],
   *   percent: true                          // "42%" in tooltip and axis
   * }
   */
  function barOption(spec, palette) {
    var suffix = spec.percent ? "%" : "";

    function formatValue(value) {
      return formatNumber(value == null ? 0 : value) + suffix;
    }

    var series = spec.series.map(function (entry) {
      var color = palette.dark ? entry.dark : entry.light;
      return {
        name: entry.label,
        type: "bar",
        barMaxWidth: 46,
        itemStyle: { color: color, borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: "series" },
        data: entry.values,
      };
    });

    return {
      textStyle: {
        fontFamily: '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
        color: palette.theme.ink,
      },
      animationDuration: 420,
      color: series.map(function (s) { return s.itemStyle.color; }),

      legend: {
        top: 0,
        left: 0,
        icon: "roundRect",
        itemWidth: 11,
        itemHeight: 11,
        itemGap: 18,
        textStyle: { color: palette.theme.ink, fontSize: 12.5 },
        inactiveColor: palette.theme.muted,
      },

      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: palette.theme.surface,
        borderColor: palette.theme.border,
        borderWidth: 1,
        padding: [8, 11],
        extraCssText: "box-shadow: 0 6px 20px rgb(18 38 63 / 12%);",
        textStyle: { color: palette.theme.ink, fontSize: 12.5 },
        formatter: function (rows) {
          if (!rows.length) return "";
          var head =
            '<div style="font-weight:700;margin-bottom:4px">' +
            escapeHtml(rows[0].axisValue) + "</div>";
          return head + rows.map(function (row) {
            return (
              '<div style="display:flex;align-items:center;gap:7px">' +
              row.marker +
              '<span style="flex:1">' + escapeHtml(row.seriesName) + "</span>" +
              '<span style="font-weight:700">' +
              escapeHtml(formatValue(row.value)) +
              "</span></div>"
            );
          }).join("");
        },
      },

      grid: { top: 42, left: 6, right: 12, bottom: 42, containLabel: true },

      xAxis: {
        type: "category",
        data: spec.categories,
        axisLine: { lineStyle: { color: palette.theme.axis } },
        axisTick: { show: false },
        axisLabel: {
          color: palette.theme.muted,
          fontSize: 11.5,
          hideOverlap: true,
          interval: 0,           // every band labelled -- there are only a few
        },
      },

      // Deliberately singular -- see the header note.
      yAxis: {
        type: "value",
        minInterval: 1,
        max: spec.percent ? 100 : null,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: palette.theme.muted,
          fontSize: 11.5,
          formatter: formatValue,
        },
        splitLine: { lineStyle: { color: palette.theme.grid } },
      },

      toolbox: {
        right: 8,
        bottom: 0,
        itemSize: 14,
        itemGap: 10,
        iconStyle: { borderColor: palette.theme.muted },
        emphasis: { iconStyle: { borderColor: palette.theme.ink } },
        feature: {
          saveAsImage: {
            title: "Descargar imagen",
            name: spec.exportName || "grafico",
            backgroundColor: palette.theme.surface,
          },
        },
      },

      series: series,
    };
  }

  /**
   * Shared mounting for both chart kinds: init, redraw on resize and
   * color-scheme change, and a controller with update/resize/destroy.
   * Charts in a swapped panel mount at width 0, which is why resize is not
   * left to the caller.
   */
  function mount(el, spec, buildOption) {
    var chart = window.echarts.init(el, null, { renderer: "canvas" });
    var current = spec;

    function palette() {
      var dark = onDarkSurface(el);
      return { dark: dark, theme: dark ? THEME.dark : THEME.light };
    }

    function draw() {
      // notMerge: series come and go between periods, and a merge would
      // leave a vanished series' marks on the canvas.
      chart.setOption(buildOption(current, palette(), el.clientWidth), true);
    }

    draw();

    var observer = null;
    if (window.ResizeObserver) {
      observer = new ResizeObserver(function () {
        chart.resize();
        // The end labels are width-gated, so a resize can cross the
        // threshold and needs a redraw, not just a resize.
        draw();
      });
      observer.observe(el);
    }

    var media = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
    function onScheme() { draw(); }
    if (media && media.addEventListener) media.addEventListener("change", onScheme);

    return {
      update: function (next) { current = next; draw(); },
      resize: function () { chart.resize(); },
      destroy: function () {
        if (observer) observer.disconnect();
        if (media && media.removeEventListener) {
          media.removeEventListener("change", onScheme);
        }
        chart.dispose();
      },
    };
  }

  /** Mount a multi-series daily line chart into `el`. */
  function line(el, spec) {
    return mount(el, spec, lineOption);
  }

  /** Mount a categorical bar chart into `el`. */
  function bar(el, spec) {
    return mount(el, spec, barOption);
  }

  window.StatsChart = {
    line: line,
    bar: bar,
    formatNumber: formatNumber,
    shortDate: shortDate,
  };
})();
