import { useEffect, useRef, useState } from "react";
import {
  Catalog,
  Issue,
  MAX_IDEA_LENGTH,
  RunResult,
  RunStageResult,
  SupportedSpec,
  Trace,
  artifactUrl,
  fetchArtifactJson,
  fetchArtifactText,
  fetchCatalog,
  fetchTrace,
  startRun,
  waitForRun,
} from "./api";
import PromptForm from "./components/PromptForm";
import ProgressStepper from "./components/ProgressStepper";
import VerificationBadges from "./components/VerificationBadges";
import {
  ArtifactErrors,
  DownloadsPanel,
  FailurePanel,
  FirmwarePanel,
  GuidePanel,
  PartsPanel,
  RefusalPanel,
  ValidationPanel,
  WiringPanel,
} from "./components/Results";
import StlViewer from "./StlViewer";
import { Theme, useTheme } from "./theme";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function collectIssues(run: RunResult): Issue[] {
  const seen = new Set<string>();
  const issues: Issue[] = [];
  for (const issue of [...run.issues, ...run.stages.flatMap((stage) => stage.issues)]) {
    const key = `${issue.code}|${issue.severity}|${issue.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    issues.push(issue);
  }
  return issues;
}

function themeLabel(theme: Theme): string {
  return theme === "dark" ? "Use light theme" : "Use dark theme";
}

export default function App() {
  const [idea, setIdea] = useState("");
  const [run, setRun] = useState<RunResult | null>(null);
  const [spec, setSpec] = useState<SupportedSpec | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [sketch, setSketch] = useState("");
  const [guide, setGuide] = useState("");
  const [traceError, setTraceError] = useState("");
  const [firmwareError, setFirmwareError] = useState("");
  const [guideError, setGuideError] = useState("");
  const [artifactErrors, setArtifactErrors] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingArtifacts, setLoadingArtifacts] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  function resetResult() {
    setRun(null);
    setSpec(null);
    setTrace(null);
    setSketch("");
    setGuide("");
    setTraceError("");
    setFirmwareError("");
    setGuideError("");
    setArtifactErrors([]);
  }

  async function loadArtifacts(result: RunResult, signal: AbortSignal) {
    setLoadingArtifacts(true);
    const tasks: { label: string; promise: Promise<void> }[] = [];

    if (result.artifacts.spec) {
      tasks.push({
        label: "Parts list",
        promise: fetchArtifactJson<SupportedSpec>(result.run_id, "spec.json", signal).then((payload) => {
          if (!signal.aborted) setSpec(payload);
        }),
      });
    }
    if (result.artifacts.trace) {
      tasks.push({
        label: "Verification trace",
        promise: fetchTrace(result.run_id, signal).then((payload) => {
          if (signal.aborted) return;
          setTrace(payload);
          setTraceError("");
        }),
      });
    } else {
      setTraceError("No verification trace was generated for this run.");
    }
    if (result.artifacts.sketch) {
      tasks.push({
        label: "Firmware",
        promise: fetchArtifactText(result.run_id, "sketch.ino", signal).then((text) => {
          if (signal.aborted) return;
          setSketch(text);
          setFirmwareError("");
        }),
      });
    }
    if (result.artifacts.guide) {
      tasks.push({
        label: "Build guide",
        promise: fetchArtifactText(result.run_id, "GUIDE.md", signal).then((text) => {
          if (signal.aborted) return;
          setGuide(text);
          setGuideError("");
        }),
      });
    }
    if (!catalog) {
      tasks.push({
        label: "Catalog",
        promise: fetchCatalog(signal).then((payload) => {
          if (!signal.aborted) setCatalog(payload);
        }),
      });
    }

    const outcomes = await Promise.allSettled(tasks.map((task) => task.promise));
    if (signal.aborted) return;

    const failures: string[] = [];
    outcomes.forEach((outcome, index) => {
      if (outcome.status === "rejected") {
        const message = errorMessage(outcome.reason);
        const label = tasks[index].label;
        failures.push(`${label}: ${message}`);
        if (label === "Verification trace") setTraceError(message);
        if (label === "Firmware") setFirmwareError(message);
        if (label === "Build guide") setGuideError(message);
      }
    });
    setArtifactErrors(failures);
    setLoadingArtifacts(false);
  }

  async function generate() {
    const text = idea.trim();
    if (loading || text.length === 0 || text.length > MAX_IDEA_LENGTH) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError("");
    resetResult();

    try {
      const runId = await startRun(text, controller.signal);
      const result = await waitForRun(
        runId,
        (payload) => {
          if (!controller.signal.aborted) setRun(payload);
        },
        { signal: controller.signal },
      );
      setRun(result);
      if (result.status === "ok") {
        await loadArtifacts(result, controller.signal);
      }
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  const validationStage: RunStageResult | null =
    run?.stages.find((stage) => stage.name === "validation") ?? null;
  const issues = run ? collectIssues(run) : [];
  const planStage = run?.stages.find((stage) => stage.name === "plan");
  const outcomeReason = planStage?.reason ?? run?.issues[0]?.message ?? "";
  const canViewStl = Boolean(run?.artifacts.base_stl && run.artifacts.lid_stl);
  const assembledUrl = run?.artifacts.assembled_stl
    ? artifactUrl(run.run_id, "assembled.stl")
    : null;

  return (
    <div className="page">
      <header className="site-header">
        <a className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">
            SP
          </span>
          <span>
            <strong>SparkPlan</strong>
            <span className="brand-subtitle">Hardware, checked.</span>
          </span>
        </a>
        <div className="header-actions">
          <span className="header-note">ESP32 parts catalog</span>
          <button
            type="button"
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={themeLabel(theme)}
            title={themeLabel(theme)}
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>

      <main className="app-shell">
        <section className="hero">
          <p className="eyebrow">Plain-language hardware planning</p>
          <h1>Ideas in. Validated builds out.</h1>
          <p className="hero-copy">
            SparkPlan turns a hardware idea into a checked parts list, wiring, a 3D-printable
            enclosure, firmware, and a build guide.
          </p>
        </section>

        <PromptForm
          idea={idea}
          onIdeaChange={setIdea}
          onSubmit={() => void generate()}
          loading={loading}
        />

        {error && (
          <div className="status-banner status-error" role="alert">
            <div>
              <strong>Could not complete the run.</strong>
              <p>{error}</p>
            </div>
            <button type="button" className="ghost-button" onClick={() => void generate()}>
              Retry
            </button>
          </div>
        )}

        {run?.status === "running" && (
          <div className="status-banner status-info" role="status">
            <div>
              <strong>Building your plan...</strong>
              <p>
                Run <code>{run.run_id}</code> is queued or in progress. This page polls for
                updates.
              </p>
            </div>
          </div>
        )}

        {(run || loading) && (
          <ProgressStepper run={run} running={Boolean(loading && run?.status !== "ok" && run?.status !== "failed" && run?.status !== "refused" && run?.status !== "unsupported")} />
        )}

        {run?.status === "ok" && (
          <>
            <div className="status-banner status-success" role="status">
              <div>
                <strong>{loadingArtifacts ? "Build ready. Loading files..." : "Build ready."}</strong>
                <p>
                  Run <code>{run.run_id}</code> passed the pipeline. Review the checks before
                  building.
                </p>
              </div>
            </div>

            <VerificationBadges verification={trace?.verification ?? null} traceError={traceError} />

            <div className="results-grid">
              <div className="results-column">
                <PartsPanel spec={spec} catalog={catalog} loading={loadingArtifacts && !spec} />
                <WiringPanel spec={spec} />
              </div>
              <div className="results-column">
                <ValidationPanel issues={issues} validationStage={validationStage} />
              </div>
            </div>

            {canViewStl && (
              <section className="panel viewer-panel" aria-labelledby="enclosure-heading">
                <div className="panel-heading">
                  <div>
                    <p className="eyebrow">STL preview</p>
                    <h2 id="enclosure-heading">Enclosure</h2>
                  </div>
                  <span className="muted">Base, lid, assembled</span>
                </div>
                <StlViewer
                  baseUrl={artifactUrl(run.run_id, "base.stl")}
                  lidUrl={artifactUrl(run.run_id, "lid.stl")}
                  assembledUrl={assembledUrl}
                  theme={theme}
                />
              </section>
            )}

            <div className="results-grid">
              <FirmwarePanel code={sketch} error={firmwareError} loading={loadingArtifacts && !sketch} />
              <GuidePanel guide={guide} error={guideError} loading={loadingArtifacts && !guide} />
            </div>

            <DownloadsPanel run={run} />
          </>
        )}

        {run && (run.status === "refused" || run.status === "unsupported") && (
          <RefusalPanel run={run} />
        )}

        {run?.status === "failed" && <FailurePanel run={run} reason={outcomeReason} />}

        <ArtifactErrors errors={artifactErrors} />

        <footer className="site-footer">
          <p>
            SparkPlan only recommends parts in its catalog. Every build is software-checked, not
            physically tested.
          </p>
        </footer>
      </main>
    </div>
  );
}
