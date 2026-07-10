/* Chart hydration: fetch history JSON and mount uPlot instances.
 *
 * Two mount points:
 *   #trend-charts  [data-history-url]  — one small chart per metric key (repo page)
 *   #compare-chart [data-compare-url]  — one chart, one series per repo (compare page)
 */
(function () {
  "use strict";

  var PALETTE = [
    "#bd4f2b", "#41576f", "#356a4d", "#d58a1f",
    "#8a4a3b", "#65716b", "#7a5410", "#a03123",
  ];

  function toEpochs(dates) {
    return dates.map(function (d) { return new Date(d + "T00:00:00Z").getTime() / 1000; });
  }

  function baseOpts(title, width) {
    return {
      title: title,
      width: width,
      height: 180,
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true } },
      axes: [
        {},
        { size: 56, values: function (u, vals) { return vals.map(function (v) { return v; }); } },
      ],
    };
  }

  function mountTrends(root) {
    fetch(root.dataset.historyUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.dates.length < 2) return;
        var x = toEpochs(data.dates);
        Object.keys(data.series).forEach(function (key, i) {
          var values = data.series[key];
          if (values.every(function (v) { return v === null; })) return;
          var cell = document.createElement("div");
          cell.className = "chart-cell";
          var h = document.createElement("h3");
          h.textContent = key;
          cell.appendChild(h);
          root.appendChild(cell);
          var opts = baseOpts("", cell.clientWidth - 24);
          opts.series = [
            {},
            { label: key.split(".").pop(), stroke: PALETTE[i % PALETTE.length], width: 2, spanGaps: true },
          ];
          new uPlot(opts, [x, values], cell);
        });
      });
  }

  function mountCompare(root) {
    fetch(root.dataset.compareUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        // Union of all dates across repos -> aligned series with gaps.
        var dateSet = {};
        Object.keys(data.repos).forEach(function (name) {
          data.repos[name].dates.forEach(function (d) { dateSet[d] = true; });
        });
        var dates = Object.keys(dateSet).sort();
        if (dates.length === 0) {
          root.textContent = "No committed metrics history for this key yet.";
          return;
        }
        var x = toEpochs(dates);
        var index = {};
        dates.forEach(function (d, i) { index[d] = i; });

        var seriesDefs = [{}];
        var seriesData = [x];
        Object.keys(data.repos).forEach(function (name, i) {
          var repo = data.repos[name];
          var aligned = new Array(dates.length).fill(null);
          repo.dates.forEach(function (d, j) { aligned[index[d]] = repo.values[j]; });
          seriesDefs.push({
            label: name,
            stroke: PALETTE[i % PALETTE.length],
            width: 2,
            spanGaps: true,
            points: { show: true, size: 6 },
          });
          seriesData.push(aligned);
        });

        var opts = baseOpts(data.key, root.clientWidth - 24);
        opts.height = 320;
        opts.series = seriesDefs;
        new uPlot(opts, seriesData, root);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var trends = document.getElementById("trend-charts");
    if (trends && trends.dataset.historyUrl) mountTrends(trends);
    var compare = document.getElementById("compare-chart");
    if (compare && compare.dataset.compareUrl) mountCompare(compare);
  });
})();
