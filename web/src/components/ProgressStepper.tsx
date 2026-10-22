import { RunResult, STAGE_LABELS, STAGE_ORDER } from "../api";

type StepState = "pending" | "queued" | "passed" | "failed" | "skipped" | "unverified" | "unknown";

type Props = {
  run: RunResult | null;
  running: boolean;
};

function stepState(name: string, run: RunResult | null, running: boolean, index: number): StepState {
  const stage = run?.stages.find((candidate) => candidate.name === name);
  if (stage) return stage.status;
  if (!running) return run ? "unknown" : "pending";
  const firstMissing = STAGE_ORDER.findIndex((stageName) => !run?.stages.some((item) => item.name === stageName));
  return index === firstMissing ? "queued" : "pending";
}

function stateLabel(state: StepState): string {
  const labels: Record<StepState, string> = {
    pending: "Pending",
    queued: "Queued",
    passed: "Passed",
    failed: "Failed",
    skipped: "Skipped",
    unverified: "Unverified",
    unknown: "Not reported",
  };
  return labels[state];
}

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export default function ProgressStepper({ run, running }: Props) {
  const completed = run?.stages.filter((stage) => stage.status === "passed").length ?? 0;

  return (
    <section className="panel" aria-labelledby="progress-heading" aria-live="polite">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Pipeline</p>
          <h2 id="progress-heading">Progress</h2>
        </div>
        <span className="muted">
          {running ? "Working" : run ? `${completed}/${STAGE_ORDER.length} passed` : "Not started"}
        </span>
      </div>

      {running && (
        <p className="hint">
          The API reports stage results when the run completes. It does not stream partial stage
          progress.
        </p>
      )}

      <ol className="stepper">
        {STAGE_ORDER.map((name, index) => {
          const state = stepState(name, run, running, index);
          const stage = run?.stages.find((candidate) => candidate.name === name);
          const reason = stage?.reason ?? "";
          const duration = formatDuration(stage?.duration_ms);
          return (
            <li key={name} className={`step step-${state}`}>
              <span className="step-marker" aria-hidden="true">
                {index + 1}
              </span>
              <div className="step-body">
                <span className="step-name">{STAGE_LABELS[name]}</span>
                <span className="step-meta">
                  <span className="step-status">{stateLabel(state)}</span>
                  {duration && <span>{duration}</span>}
                </span>
                {reason && state !== "passed" && <span className="step-reason">{reason}</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
