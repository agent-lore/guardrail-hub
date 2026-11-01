const { test } = require('node:test');
const assert = require('node:assert/strict');
const { trendData, compareData } = require('../src/guardrail_hub/static/hub.js');

test('same-second snapshots keep distinct identities and values', () => {
  const data = { repos: { demo: { shas: ['aaaa', 'bbbb'], timestamps: ['2026-01-01T12:00:00Z', '2026-01-01T12:00:00Z'], statuses: ['measured', 'measured'], values: [3, 7] } } };
  const result = compareData(data);
  assert.deepEqual(result.data, [[0, 1], [3, 7]]);
  assert.match(result.labels[0], /aaaa/);
  assert.match(result.labels[1], /bbbb/);
});

test('missing measurements and provenance changes break trend lines', () => {
  const data = { shas: ['a', 'b', 'c'], timestamps: ['t1', 't2', 't3'], boundaries: [2], series: { k: [0, null, 5] }, value_statuses: { k: ['measured', 'unsupported', 'measured'] } };
  assert.deepEqual(trendData(data, 'k').data, [[0, 1, 2, 3], [0, null, null, 5]]);
});

test('cross-repo stale evidence is not plotted as a measured zero', () => {
  const result = compareData({ repos: { one: { shas: ['a'], timestamps: ['2026-01-01T00:00:00Z'], statuses: ['stale'], values: [0] }, two: { shas: ['b'], timestamps: ['2026-01-01T00:00:00Z'], statuses: ['measured'], values: [0] } } });
  assert.deepEqual(result.data, [[0, 1], [null, null], [null, 0]]);
});
