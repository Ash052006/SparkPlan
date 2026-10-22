import { FormEvent, useState } from "react";
import {
  ARTIFACT_LINKS,
  Issue,
  RunResult,
  RunStageResult,
  STAGE_ORDER,
  SupportedSpec,
  artifactUrl,
  bundleUrl,
  fetchArtifactText,
  startRun,
  waitForRun,
} from "./api";
import StlViewer from "./StlViewer";

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "-";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function stageLabel(name: string): string {
  return name.replace(/_/g, " ");
}

function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <ul className="issues">
      {issues.map((issue, index) => (
        <li key={`${issue.code}-${index}`}>
          <code>{issue.code}</code> [{issue.severity}] {issue.message}
          {issue.fix_hint ? ` - ${issue.fix_hint}` : ""}
        </li>
      ))}
    </ul>
  );
}

function StageList({ stages }: { stages: RunStageResult[] }) {
  const ordered = STAGE_ORDER.map((name) => stages.find((stage) => stage.name === name)).filter(
    (stage): stage is RunStageResult => stage !== undefined,
  );

  return (
    <ol className="stages">
      {ordered.map((stage) => (
        <li key={stage.name} className="stage">
          <div className="stage-row">
            <span className="stage-name">{stageLabel(stage.name)}</span>
            <span className={`badge ${stage.status}`}>{stage.status}</span>
            <span className="duration">{formatDuration(stage.duration_ms)}</span>
          </div>
          {stage.reason && <p className="reason">{stage.reason}</p>}
          <IssueList issues={stage.issues} />
        </li>
      ))}
    </ol>
  );
}

export default function App() {
  const [idea, setIdea] = useState("desk weather station showing temperature and humidity");
  const [run, setRun] = useState<RunResult | null>(null);
  const [spec, setSpec] = useState<SupportedSpec | null>(null);
  const [guide, setGuide] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setRun(null);
    setSpec(null);
    setGuide("");
    try {
      const runId = await startRun(idea);
      const result = await waitForRun(runId, setRun);
      setRun(result);

      if (result.artifacts.spec) {
        try {
          const text = await fetchArtifactText(result.run_id, "spec.json");
          setSpec(JSON.parse(text) as SupportedSpec);
        } catch (err) {
          setError(`Could not load the spec artifact: ${err instanceof Error ? err.message : err}`);
        }
      }
      if (result.artifacts.guide) {
        try {
          setGuide(await fetchArtifactText(result.run_id, "GUIDE.md"));
        } catch (err) {
          setError(
            `Could not load the build guide artifact: ${err instanceof Error ? err.message : err}`,
          );
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const availableArtifacts = run
    ? ARTIFACT_LINKS.filter((artifact) => Boolean(run.artifacts[artifact.name]))
    : [];
  const canViewStl = Boolean(run?.artifacts.base_stl && run.artifacts.lid_stl);
  const planStage = run?.stages.find((stage) => stage.name === "plan");
  const outcomeReason = planStage?.reason || run?.issues[0]?.message || "";

  return (
    <div className="app">
      <h1>SparkPlan</h1>
      <p>Describe a hardware idea. Get a validated parts list, enclosure, firmware and guide.</p>
      <form onSubmit={onSubmit}>
        <input
          type="text"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="e.g. plant monitor that beeps when dry"
        />
        <button type="submit" disabled={loading || idea.trim().length === 0}>
          {loading ? "Running..." : "Build"}
        </button>
      </form>

      {error && (
        <div className="panel">
          <span className="badge failed">error</span> {error}
        </div>
      )}

      {run && (
        <>
          <div className="panel">
            <div className="run-header">
              <span className={`badge ${run.status}`}>{run.status}</span>
              <span className="run-id">run {run.run_id}</span>
            </div>
            {run.status !== "ok" && outcomeReason && <p className="reason">{outcomeReason}</p>}
            {run.status === "running" && (
              <p className="reason">The run is queued or in progress. This page polls for updates.</p>
            )}
            <h3>Stages</h3>
            <StageList stages={run.stages} />
          </div>

          {run.issues.length > 0 && (
            <div className="panel">
              <h3>Findings</h3>
              <IssueList issues={run.issues} />
            </div>
          )}

          {spec && (
            <div className="panel">
              <h3>{spec.title || "Build spec"}</h3>
              {spec.summary && <p>{spec.summary}</p>}
              <h4>Parts</h4>
              <ul>
                {spec.parts.map((part) => (
                  <li key={part.instance_id}>
                    <code>{part.instance_id}</code> - {part.part_id}
                    {part.quantity && part.quantity > 1 ? ` x${part.quantity}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {availableArtifacts.length > 0 && (
            <div className="panel">
              <h3>Downloads</h3>
              <ul className="artifact-links">
                {availableArtifacts.map((artifact) => (
                  <li key={artifact.name}>
                    <a href={artifactUrl(run.run_id, artifact.filename)} download={artifact.filename}>
                      {artifact.filename}
                    </a>{" "}
                    <span className="muted">{artifact.label}</span>
                  </li>
                ))}
                <li>
                  <a href={bundleUrl(run.run_id)} download>
                    bundle.zip
                  </a>{" "}
                  <span className="muted">All available files</span>
                </li>
              </ul>
            </div>
          )}

          {(canViewStl || guide) && (
            <div className="grid">
              {canViewStl && (
                <div className="panel">
                  <h3>Enclosure</h3>
                  <StlViewer
                    baseUrl={artifactUrl(run.run_id, "base.stl")}
                    lidUrl={artifactUrl(run.run_id, "lid.stl")}
                  />
                </div>
              )}
              {guide && (
                <div className="panel">
                  <h3>Build guide</h3>
                  <pre>{guide}</pre>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
