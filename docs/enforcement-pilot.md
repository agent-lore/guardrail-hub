# Hub changed-code enforcement pilot

This repository enables `enforce_new` and `enforce_worsened` using the kit 1.6.0
baseline already committed on main at
`876fe38827964b4da4df98466cbcd4abd9f3725c`. This is a hub-only policy change;
the canonical kit and other repositories retain their current policies.

The purpose is to prompt useful design decisions while preventing unnoticed
growth in existing problems. Passing the gate is not proof of code quality.
Existing findings need not all disappear before a useful change can merge.

## What blocks a PR

The existing **Diagram drift** CI job regenerates the snapshot, checks committed
artifacts, then compares against the immutable PR base SHA with
`scripts/metrics_diff.py --markdown --check`. With these flags enabled:

- A newly identified function/module above its review threshold blocks.
- An existing over-threshold entity whose measured value increases blocks,
  even if a different function improves or the repository maximum stays flat.
- An exact, bounded exception accepts only its named metric and entity up to
  its limit. Growth within that bound is still reported as worsening.
- Existing unchanged findings and improvements do not block.
- Missing or incompatible measurement baselines block with a comparability
  message. Do not weaken the flags to disguise an analyzer/scope change.

Aggregate review budgets remain reported, not hard gates. Dependency contracts,
zero-cycle budgets, and zero private-access budgets remain invariants. Renames
appear as removal/addition: inspect those pairs rather than claiming an automatic
improvement. Policy edits require a design rationale in the PR because the
comparison applies the proposed policy to both snapshots.

For a local comparison, first regenerate and then use the intended PR base:

```bash
make diagrams
git show origin/main:docs/generated/metrics.json > /tmp/hub-base-metrics.json
uv run python scripts/metrics_diff.py /tmp/hub-base-metrics.json docs/generated/metrics.json --markdown --check
```

`make check` validates the current tree but does not itself compare revisions;
`make metrics-diff` displays a delta without enforcing it. Use the explicit
`--check` command above to exercise the PR gate locally. See the
[quality guide](quality-guide.md) for measurement and exception semantics.

## Reviewed baseline

These decisions accept the current design for the pilot; they are not measured
improvements. No production source was moved or rewritten to hit a threshold.

| Aggregate | Previous reference | Pilot reference | Rationale |
|---|---:|---:|---|
| Cross-component edges | 42 | 46 | Revision review adds History → Budgets to evaluate accepted policy, History → Kit to load the trusted comparison engine, Store → DocsRender for containment checks, and Web → Errors to translate repository failures into HTTP responses. These dependencies keep the existing responsibilities and dependency direction. |
| Functions over complexity 10 | 6 | 8 | `history.series` now preserves provenance/status boundaries; `repo_scan.measurement_status` validates freshness. Keep these responsibilities intact and gate future individual worsening. The eight functions below remain visible review findings. |
| Tier-skipping edges | 5 | 6 | The additional Web → Errors dependency uses the shared error type directly. Open layering permits downward imports across tiers; a forwarding module would not improve the design. This count is contextual, not a goal of zero. |

Keep the 800-line module threshold instead of ratcheting it down to the current
395-line maximum. That headroom avoids forcing premature separation. Keep the
maximum-complexity reference at 19; unchanged maximum is not a loophole in the
individual regression gate.

| Existing function | Complexity | Disposition |
|---|---:|---|
| `archmap.load_archmap` | 11 | Keep configuration validation and mapping assembly together; revisit if additional formats introduce distinct behavior. |
| `cli._cmd_drift` | 19 | Reporting branches cover text/JSON and multiple repositories; defer extraction until a reusable rendering responsibility is needed. |
| `coupling.mine_coupling` | 15 | One co-change calculation with filtering and aggregation; preserve its algorithm and review new branches. |
| `drift._FoldStringConcat.visit_BinOp` | 12 | Keep the AST normalization cases together so their equivalence is reviewable. |
| `history.series` | 16 | A cohesive chart-data projection; comprehensions contribute to complexity. Preserve unavailable values and measurement boundaries. |
| `installer._tiers_from_importlinter` | 14 | A single inference algorithm with validation; more contract conventions would warrant a new design review. |
| `installer.apply_kit` | 16 | Keep language validation, conflict detection, and installation ordering explicit. |
| `repo_scan.measurement_status` | 11 | Keep freshness decisions centralized; avoid scattering missing/invalid/stale handling. |

These are baseline dispositions, not complexity exceptions. Increasing any of
these scores is subject to the same review gate as other existing findings.

### App factory length

`guardrail_hub.web.create_app` has complexity 2 and a physical span of 292 lines.
The length measure includes nested route definitions; those handlers are also
measured individually. Their shared `RepoStore` and template closure makes the
current grouping coherent.

Accept only this function's **length**, up to **320 lines**. The 28-line margin
allows small registration changes without redesigning the app factory each
time. It does not exempt handler complexity, module size, containment checks,
or the rule that repository access goes through `RepoStore`. Revisit the decision
if handlers gain substantive orchestration, route groups acquire independent
ownership, or the factory exceeds 320 lines. Do not add business logic simply
because there is remaining length headroom.

## Evaluate before extending the pilot

Review the first ten merged hub PRs that touch measured source after this policy
lands. If fewer have landed after four weeks, review the available cases and
report the limited sample rather than claiming success. For each applicable PR,
record in its description or follow-up review:

- Which findings triggered, and whether the decision was a design improvement,
  a justified exception, or an unhelpful alert.
- The behavioral evidence supporting any claimed improvement; existing tests
  passing alone do not prove improved quality.
- Time spent regenerating artifacts and resolving generated-file conflicts,
  when actually observed; otherwise record it as unknown.
- Any later regression/revert linked to the changed behavior.

Expand to another repository only after reviewing those cases and finding useful
design decisions without repeated pressure for artificial splits. Do not require
zero findings or zero exceptions. Do not fabricate coverage, mutation, or outcome
data to fill the dashboard.

If the gate causes repeated unhelpful churn, make a reviewed policy PR to refine
the relevant exception/threshold or set the two flags back to false. Regenerate
the snapshot, preserve invariant enforcement and history, and record why the
pilot changed. Analyzer upgrades need an explicit comparability migration plan;
this pilot does not make incomparable measurements into a quality trend.
