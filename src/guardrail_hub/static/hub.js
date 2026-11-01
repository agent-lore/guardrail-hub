/* Commit identity, rather than date, determines each chart observation. */
(function () {
  "use strict";
  var PALETTE = ["#bd4f2b", "#41576f", "#356a4d", "#d58a1f", "#8a4a3b", "#65716b"];

  function trendData(data, key) {
    var x = [], y = [], labels = [], boundaries = new Set(data.boundaries || []);
    data.shas.forEach(function (sha, i) {
      if (boundaries.has(i)) {
        x.push(x.length); y.push(null); labels.push("measurement changed");
      }
      x.push(x.length);
      var status = (data.value_statuses[key] || [])[i];
      y.push(status === "measured" ? data.series[key][i] : null);
      labels.push(data.timestamps[i] + " · " + sha.slice(0, 8) + " · " + status);
    });
    return { data: [x, y], labels: labels };
  }

  function compareData(data) {
    var observations = [];
    Object.keys(data.repos).forEach(function (name) {
      var repo = data.repos[name];
      repo.shas.forEach(function (sha, i) {
        observations.push({ name: name, sha: sha, timestamp: repo.timestamps[i], value: repo.values[i], status: repo.statuses[i] });
      });
    });
    observations.sort(function (a, b) {
      return Date.parse(a.timestamp) - Date.parse(b.timestamp) || a.name.localeCompare(b.name) || a.sha.localeCompare(b.sha);
    });
    var names = Object.keys(data.repos);
    return {
      names: names,
      labels: observations.map(function (o) { return o.timestamp + " · " + o.name + " · " + o.sha.slice(0, 8) + " · " + o.status; }),
      data: [observations.map(function (_, i) { return i; })].concat(names.map(function (name) {
        return observations.map(function (o) { return o.name === name && o.status === "measured" ? o.value : null; });
      })),
    };
  }

  function options(labels, width, height) {
    return {
      width: Math.max(250, width), height: height,
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: false } },
      axes: [{ values: function (_, vals) { return vals.map(function (v) { return Number.isInteger(v) && labels[v] ? labels[v].slice(0, 10) : ""; }); } }, { size: 56 }],
      series: [{ label: "Commit", value: function (_, v) { return labels[v] || "—"; } }],
    };
  }

  function load(root, url, render) {
    fetch(url).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(render).catch(function (e) { root.textContent = "Chart unavailable: " + e.message; });
  }

  function mountTrends(root) {
    load(root, root.dataset.historyUrl, function (data) {
      Object.keys(data.series).forEach(function (key, i) {
        var chart = trendData(data, key);
        var cell = document.createElement("div"); cell.className = "chart-cell";
        var h = document.createElement("h3"); h.textContent = key; cell.appendChild(h); root.appendChild(cell);
        if (chart.data[1].every(function (v) { return v === null; })) {
          var note = document.createElement("p"); note.textContent = "No measured values (missing, unsupported, or stale)."; cell.appendChild(note); return;
        }
        var opts = options(chart.labels, cell.clientWidth - 24, 200);
        opts.series.push({ label: key, stroke: PALETTE[i % PALETTE.length], width: 2, spanGaps: false, points: { show: true, size: 5 } });
        new uPlot(opts, chart.data, cell);
      });
    });
  }

  function mountCompare(root) {
    load(root, root.dataset.compareUrl, function (data) {
      var chart = compareData(data);
      if (!chart.labels.length) { root.textContent = "No committed history yet."; return; }
      var opts = options(chart.labels, root.clientWidth - 24, 320);
      chart.names.forEach(function (name, i) {
        opts.series.push({ label: name, stroke: PALETTE[i % PALETTE.length], width: 0, spanGaps: false, points: { show: true, size: 7 } });
      });
      new uPlot(opts, chart.data, root);
    });
  }

  if (typeof module !== "undefined") module.exports = { trendData: trendData, compareData: compareData };
  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", function () {
    var trends = document.getElementById("trend-charts"), compare = document.getElementById("compare-chart");
    if (trends) mountTrends(trends);
    if (compare) mountCompare(compare);
  });
})();
