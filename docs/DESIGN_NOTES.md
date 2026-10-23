# Design notes: three real failures

These notes come from `docs/PROGRESS.md` and the git history. They are not
hypothetical interview answers.

## 1. The repair loop regenerated the build instead of repairing it

**Failure.** A live desk-plant-monitor plan ended `failed` after the full repair
budget with 12 validation errors. The model kept adding an unrequested passive
buzzer and kept losing wiring and layout entries. The errors did not shrink
between rounds.

**Cause.** Two problems compounded:

- The prompt ordered keys `parts, layout, behaviors, connections`, and the
  completion cap cut the tail. `connections` was the first thing lost, so the
  model produced missing `NO_POWER`, `NO_GROUND` and `PIN_UNCONNECTED` wiring.
- The repair prompt sent only the original idea and the error list, not the
  candidate spec. The model therefore re-derived the entire build each round
  instead of changing only what the validators flagged.

The schema also did not require `layout` or `connections`; omitting them was
schema-valid, so prompt guidance was the only thing asking for them.

**Fix.** The repair prompt now includes a compact `CURRENT_SPEC` anchor, the
current error-severity issues with `part_ids`, and an explicit checklist:
preserve valid parts, connections and layout; change only what the errors
require; do not add components the user did not request; ensure every non-board
part has power and ground and a layout entry. The call bound stayed unchanged.
The fix is pinned by `tests/planner/phase3/test_p3_convergence.py`, with the
offline reproduction in
`tests/fixtures/phase3/attempts/desk_monitor_missing_wiring.json`.

Commits: `695fb19` (fix) and `b8bcd9a` (root-cause notes). A later real trace,
[`docs/phase9/repair-trace.json`](phase9/repair-trace.json), shows a different
build repairing three validator errors in one round.

## 2. Enclosure wall sizing counted the same wall run twice

**Failure.** Phase 4 geometry tests produced spurious `GEOMETRY_WALL_FULL`
errors for wall-mounted parts that fit. The layout was rejecting valid packs.

**Cause.** The cavity sizing check compared the part's dimensions against
`2 * keep`, which over-counted the wall runs. A wall-mounted part only has to
fit between the two opposing wall strips, not against two copies of the
clearance on each side.

**Fix.** Wall-mounted parts are measured against the two opposing wall strips:
the maximum of the `-y/+y` runs and the `-x/+x` runs. The clearance guarantee
stayed in place; the double-counting did not. The layout and geometry tests
cover the packing and clearance cases.

Reference: Phase 4 in `docs/PROGRESS.md`; the layout engine is
`sparkplan/geometry/layout.py` from commit `d0e9c1e`.

## 3. The firmware looked plausible but did not compile

**Failure.** The first real `arduino-cli` compile failed with:

```text
error: 'GPIO4' was not declared in this scope
```

A second defect was latent: the legacy renderer emitted a float through a `%s`
conversion, which is undefined behaviour. A rule or display line that could not
be rendered could also still produce a sketch.

**Cause.** The legacy renderer printed the catalog's board pin label (`GPIO4`)
where the ESP32 core expects the GPIO number (`4`). The format string was not
matched to the argument type, and the renderer did not fail closed when a
template fragment was unresolved.

**Fix.** The renderer now resolves the GPIO number from the catalog board
pinout instead of guessing, converts the float format to `%.1f` and casts the
argument to match. An unresolved rule or display line marks the build
`supported = False` with a machine-readable issue instead of shipping a broken
sketch. The firmware compile wrapper reports `ARDUINO_CLI_MISSING`,
`COMPILE_ERROR` and the other tool failures explicitly.

Reference: “Fixes found by compiling for real” in the Phase 5 section of
`docs/PROGRESS.md`; the behaviour-based firmware work is commit `96518dd`, with
the compile wrapper in `c11922b`.
