# 60-second SparkPlan demo

This script uses the checked-in traces so the demo does not depend on the LLM
provider being available. The repair trace is from a real run; the refusal trace
is a deterministic `FakeLLM` trace and is labelled as such.

## Before the clock starts

```sh
uv run uvicorn api.app:app --host 127.0.0.1 --port 8000
```

In another shell, start the UI if you want to show it:

```sh
cd web
npm run dev
```

Keep these tabs open:

- the SparkPlan form or the API docs at `http://127.0.0.1:8000/docs`
- [`docs/phase9/repair-trace.json`](docs/phase9/repair-trace.json)
- [`docs/phase9/refusal-trace.json`](docs/phase9/refusal-trace.json)

## 0:00–0:10 — Set the frame

Say: **“SparkPlan turns a hardware idea into a validated build package. The LLM
only proposes structured data; code validates it.”**

Point at the architecture section in the README or the Mermaid diagram. Do not
claim a physical build or simulation.

## 0:10–0:30 — Repair loop

Type this prompt:

```text
A motion display that lights up when a PIR sensor detects movement.
```

If the live LLM is configured, submit it through the UI. If it is not, open
[`docs/phase9/repair-trace.json`](docs/phase9/repair-trace.json) and scroll to
`stages[0].details.attempts`.

Point at:

- `stages[0].details.attempts[0].issues`: three errors —
  `ACTUATOR_NO_CONTROL_GPIO`, `BEHAVIOR_UNKNOWN_READING`, `RAIL_OUT_OF_RANGE`.
- `stages[0].details.attempts[1].kind`: `"ok"`.
- `stages[0].details.repair_rounds`: `1`.
- `stages[0].details.llm_calls`: `2`.
- the pipeline stages: `plan`, `validation`, `geometry`, `slicing`,
  `firmware_generation`, `firmware_compile`, `guide` all `passed`.

Say: **“The first answer was invalid. The validators returned structured issues,
the planner repaired once, and the second answer passed every stage. The run is
bounded; it cannot retry forever.”**

If showing the UI, point at the validation summary, the STL viewer, the
firmware sketch, the build guide and the bundle download. Point out
`Simulated: Skipped` and `Not physically built.`

## 0:30–0:45 — Refusal

Open [`docs/phase9/refusal-trace.json`](docs/phase9/refusal-trace.json). This is a
deterministic `FakeLLM` refusal trace, not a live-model run.

The prompt used to produce it was:

```text
a self-balancing personal transporter
```

Point at:

- `stages[0].details.plan_status`: `"refused"`.
- `stages[0].issues[0].code`: `UNSUPPORTED_IDEA`.
- `stages[0].issues[0].message`: the two reasons. It says the build needs
  brushless hub motors, a motor controller and an IMU stabilization loop, and
  that the catalog has no matching parts.
- `validation` is `failed` because the plan was refused; `geometry`, `slicing`,
  `firmware_generation`, `firmware_compile` and `guide` are `skipped`; the
  `simulation` stage is `unverified`.
- there is no `spec.json`, `base.stl`, firmware or guide.

Say: **“It refuses rather than substituting a similar part. The reason is
machine-readable and returned to the caller.”**

## 0:45–0:60 — Close

Say: **“The verified claims are deterministic validation, firmware compilation
and STL slicing. Simulation is not implemented, and nothing has been physically
built. Those are separate claims in the trace and in the README.”**

Point at the Verification and Limitations sections of the README.

## Optional live refusal contrast

If the LLM is configured, submit:

```text
An autonomous drone with GPS and camera.
```

The live Phase 8 UI run returned reasons, missing capabilities and a closest
supported alternative. That run did not persist a `trace.json`; use the
checked-in deterministic refusal trace for the on-screen JSON. Do not present
the deterministic trace as a live run.
