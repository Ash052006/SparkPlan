import { useEffect, useRef, useState } from "react";
import {
  Catalog,
  MAX_IDEA_LENGTH,
  RunResult,
  RunStageResult,
  SupportedSpec,
  Trace,
  artifactUrl,
  bundleUrl,
  fetchArtifactJson,
  fetchArtifactText,
  fetchCatalog,
  fetchTrace,
  modifyRun,
  startRun,
  waitForRun,
} from "./api";
import PromptForm from "./components/PromptForm";
import ProgressStepper from "./components/ProgressStepper";
import VerificationBadges from "./components/VerificationBadges";
import OptimizationSuggestions from "./components/OptimizationSuggestions";
import Sidebar from "./components/Sidebar";
import { ChatSession, ChatTurn } from "./types/chat";
import {
  ArtifactErrors,
  DownloadsPanel,
  FailurePanel,
  FirmwarePanel,
  GuidePanel,
  PartsPanel,
  RefusalPanel,
  StageFindingsPanel,
  ValidationPanel,
  WiringPanel,
} from "./components/Results";
import SimulationPanel from "./components/SimulationPanel";
import { WokwiDiagram } from "./components/WokwiDiagramViewer";
import HardwareFlasher from "./components/HardwareFlasher";
import FabricationModal from "./components/FabricationModal";
import StlViewer from "./StlViewer";
import { Theme, useTheme } from "./theme";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function themeLabel(theme: Theme): string {
  return theme === "dark" ? "Use light theme" : "Use dark theme";
}

export default function App() {
  const [idea, setIdea] = useState("");
  const [sessions, setSessions] = useState<ChatSession[]>([
    {
      id: "session-1",
      title: "New Hardware Build",
      turns: [],
      createdAt: new Date(),
    },
  ]);
  const [activeSessionId, setActiveSessionId] = useState<string>("session-1");
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth >= 900;
  });
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [bottomInput, setBottomInput] = useState("");
  const [theme, toggleTheme] = useTheme();
  const [activeFlasherRunId, setActiveFlasherRunId] = useState<string | null>(null);
  const [activeFab, setActiveFab] = useState<{ runId: string; spec: SupportedSpec | null } | null>(null);

  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
  const turns = activeSession ? activeSession.turns : [];

  const abortRef = useRef<AbortController | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const bottomInputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    if (turns.length > 0) {
      chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [turns.length, loading]);

  function updateTurn(id: string, patch: Partial<ChatTurn> | ((prev: ChatTurn) => Partial<ChatTurn>)) {
    setSessions((prev) =>
      prev.map((s) => {
        if (s.id !== activeSessionId) return s;
        return {
          ...s,
          turns: s.turns.map((t) => {
            if (t.id !== id) return t;
            const updates = typeof patch === "function" ? patch(t) : patch;
            return { ...t, ...updates };
          }),
        };
      }),
    );
  }

  function handleNewSession() {
    const newId = `session-${Date.now()}`;
    const newSession: ChatSession = {
      id: newId,
      title: "New Hardware Build",
      turns: [],
      createdAt: new Date(),
    };
    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newId);
    setIdea("");
    setBottomInput("");
  }

  function handleDeleteSession(id: string) {
    setSessions((prev) => {
      const next = prev.filter((s) => s.id !== id);
      if (next.length === 0) {
        const freshId = `session-${Date.now()}`;
        return [{ id: freshId, title: "New Hardware Build", turns: [], createdAt: new Date() }];
      }
      return next;
    });
    if (activeSessionId === id) {
      const remaining = sessions.filter((s) => s.id !== id);
      if (remaining.length > 0) {
        setActiveSessionId(remaining[0].id);
      }
    }
  }

  async function loadArtifactsForTurn(turnId: string, result: RunResult, signal: AbortSignal) {
    updateTurn(turnId, { loadingArtifacts: true });
    const tasks: { label: string; promise: Promise<void> }[] = [];

    if (result.artifacts.spec) {
      tasks.push({
        label: "Parts list",
        promise: fetchArtifactJson<SupportedSpec>(result.run_id, "spec.json", signal).then((payload) => {
          if (!signal.aborted) updateTurn(turnId, { spec: payload });
        }),
      });
    }
    if (result.artifacts.trace) {
      tasks.push({
        label: "Verification trace",
        promise: fetchTrace(result.run_id, signal).then((payload) => {
          if (signal.aborted) return;
          updateTurn(turnId, { trace: payload, traceError: "" });
        }),
      });
    } else {
      updateTurn(turnId, { traceError: "No verification trace was generated for this run." });
    }
    if (result.artifacts.sketch) {
      tasks.push({
        label: "Firmware",
        promise: fetchArtifactText(result.run_id, "sketch.ino", signal).then((text) => {
          if (signal.aborted) return;
          updateTurn(turnId, { sketch: text, firmwareError: "" });
        }),
      });
    }
    if (result.artifacts.guide) {
      tasks.push({
        label: "Build guide",
        promise: fetchArtifactText(result.run_id, "GUIDE.md", signal).then((text) => {
          if (signal.aborted) return;
          updateTurn(turnId, { guide: text, guideError: "" });
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

    if (result.artifacts.simulation_diagram) {
      updateTurn(turnId, { loadingDiagram: true });
      tasks.push({
        label: "Wokwi diagram",
        promise: fetchArtifactJson<WokwiDiagram>(result.run_id, "diagram.json", signal).then(
          (payload) => {
            if (!signal.aborted) {
              updateTurn(turnId, { wokwiDiagram: payload, loadingDiagram: false });
            }
          },
        ),
      });
    }

    if (result.artifacts.simulation_log) {
      updateTurn(turnId, { loadingLog: true });
      tasks.push({
        label: "Serial log",
        promise: fetchArtifactText(result.run_id, "simulation.log", signal).then((text) => {
          if (!signal.aborted) {
            updateTurn(turnId, { serialLog: text, loadingLog: false });
          }
        }),
      });
    }

    const outcomes = await Promise.allSettled(tasks.map((task) => task.promise));
    if (signal.aborted) return;

    const failures: string[] = [];
    let tErr = "";
    let fErr = "";
    let gErr = "";
    outcomes.forEach((outcome, index) => {
      if (outcome.status === "rejected") {
        const message = errorMessage(outcome.reason);
        const label = tasks[index].label;
        failures.push(`${label}: ${message}`);
        if (label === "Verification trace") tErr = message;
        if (label === "Firmware") fErr = message;
        if (label === "Build guide") gErr = message;
      }
    });

    updateTurn(turnId, (prev) => ({
      artifactErrors: failures,
      loadingArtifacts: false,
      traceError: tErr || prev.traceError,
      firmwareError: fErr || prev.firmwareError,
      guideError: gErr || prev.guideError,
    }));
  }

  async function handleSend(promptText: string) {
    const text = promptText.trim();
    if (loading || text.length === 0 || text.length > MAX_IDEA_LENGTH) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setBottomInput("");
    setIdea("");

    // Identify if there is an existing successful build to modify
    const lastSuccessfulTurn = [...turns].reverse().find((t) => t.run?.status === "ok");
    const baseRunId = lastSuccessfulTurn?.run?.run_id;

    const turnId = `turn-${Date.now()}`;
    const newTurn: ChatTurn = {
      id: turnId,
      prompt: text,
      run: null,
      spec: null,
      trace: null,
      sketch: "",
      guide: "",
      wokwiDiagram: null,
      serialLog: "",
      loading: true,
      loadingArtifacts: false,
      loadingDiagram: false,
      loadingLog: false,
      traceError: "",
      firmwareError: "",
      guideError: "",
      artifactErrors: [],
      error: "",
      studioTab: "all",
      timestamp: new Date(),
    };

    // Auto-name active session title if it is still default
    if (activeSession && (activeSession.title === "New Hardware Build" || activeSession.turns.length === 0)) {
      const cleanTitle = text.length > 28 ? `${text.slice(0, 28)}…` : text;
      setSessions((prev) =>
        prev.map((s) => (s.id === activeSessionId ? { ...s, title: cleanTitle } : s)),
      );
    }

    setSessions((prev) =>
      prev.map((s) => {
        if (s.id !== activeSessionId) return s;
        return { ...s, turns: [...s.turns, newTurn] };
      }),
    );

    try {
      let runId: string;
      if (baseRunId) {
        runId = await modifyRun(baseRunId, text, controller.signal);
      } else {
        runId = await startRun(text, controller.signal);
      }

      const result = await waitForRun(
        runId,
        (payload) => {
          if (!controller.signal.aborted) {
            updateTurn(turnId, { run: payload });
          }
        },
        { signal: controller.signal },
      );

      updateTurn(turnId, { run: result, loading: false });

      if (result.status === "ok") {
        await loadArtifactsForTurn(turnId, result, controller.signal);
      }
    } catch (caught) {
      if (!isAbort(caught)) {
        updateTurn(turnId, { loading: false, error: errorMessage(caught) });
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return (
    <div className="chatgpt-layout page">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
        onSelectSession={(id) => {
          setActiveSessionId(id);
          setBottomInput("");
          setIdea("");
        }}
        onNewSession={handleNewSession}
        onDeleteSession={handleDeleteSession}
      />

      <div className={`chatgpt-main-viewport ${sidebarOpen ? "sidebar-open" : "sidebar-collapsed"}`}>
        <header className="site-header">
          <div className="header-brand-wrap">
            <button
              type="button"
              className="sidebar-hamburger-btn"
              onClick={() => setSidebarOpen((v) => !v)}
              aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
              title="Toggle chat history"
            >
              ☰
            </button>
            <a className="brand" href="/">
              <span className="brand-mark" aria-hidden="true">
                SP
              </span>
              <span>
                <strong>SparkPlan</strong>
                <span className="brand-subtitle">Hardware, checked.</span>
              </span>
            </a>
          </div>
          <div className="header-actions">
            <div className="telemetry-bar" aria-label="System status">
              <span className="telemetry-pill live">
                <span className="telemetry-dot" aria-hidden="true" />
                ONLINE
              </span>
              <span className="telemetry-pill">20 MODULES</span>
              <span className="telemetry-pill">ESP32-WROOM-32</span>
            </div>
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

      <main className="app-shell chat-shell">
        {turns.length === 0 && (
          <>
            <section className="hero hero-awwwards">
              <div className="hero-glow-layer" aria-hidden="true" />
              <div className="hero-content">
                <div className="hero-eyebrow-wrap">
                  <span className="hero-pill">Autonomous Hardware Studio</span>
                  <span className="hero-separator">/</span>
                  <span className="hero-pill-sub">LLM Proposes · Code Verifies</span>
                </div>
                <h1 className="hero-title">Ideas in. Validated builds out.</h1>
                <p className="hero-copy">
                  SparkPlan compiles plain-language ideas into verified pinouts, 3D-printable
                  parametric CAD, circuit simulation, and compilable C++ firmware.
                </p>
              </div>
            </section>

            <PromptForm
              idea={idea}
              onIdeaChange={setIdea}
              onSubmit={() => void handleSend(idea)}
              loading={loading}
            />
          </>
        )}

        {/* ── CONVERSATION THREAD (CHATGPT STYLE) ── */}
        {turns.length > 0 && (
          <div className="chat-thread" role="log" aria-label="Hardware build conversation">
            {turns.map((turn) => {
              const validationStage: RunStageResult | null =
                turn.run?.stages.find((stage) => stage.name === "validation") ?? null;
              const planStage = turn.run?.stages.find((stage) => stage.name === "plan");
              const outcomeReason = planStage?.reason ?? turn.run?.issues[0]?.message ?? "";
              const canViewStl = Boolean(turn.run?.artifacts.base_stl && turn.run.artifacts.lid_stl);
              const assembledUrl = turn.run?.artifacts.assembled_stl
                ? artifactUrl(turn.run.run_id, "assembled.stl")
                : null;

              return (
                <article key={turn.id} className="chat-turn-card">
                  {/* User Turn */}
                  <div className="chat-user-row">
                    <div className="chat-user-avatar" aria-hidden="true">
                      👤
                    </div>
                    <div className="chat-user-bubble">
                      <span className="chat-user-label">You</span>
                      <p className="chat-user-text">{turn.prompt}</p>
                    </div>
                  </div>

                  {/* Assistant Turn */}
                  <div className="chat-assistant-row">
                    <div className="chat-assistant-body">
                      <div className="chat-assistant-header">
                        <div className="chat-assistant-avatar" aria-hidden="true">
                          SP
                        </div>
                        <span className="chat-assistant-brand">SparkPlan Compiler</span>
                        {turn.run && (
                          <span className="chat-run-chip">
                            RUN <code>{turn.run.run_id}</code>
                          </span>
                        )}
                      </div>

                      {turn.error && (
                        <div className="status-banner status-error" role="alert">
                          <div>
                            <strong>Could not complete the run.</strong>
                            <p>{turn.error}</p>
                          </div>
                          <button
                            type="button"
                            className="ghost-button"
                            onClick={() => void handleSend(turn.prompt)}
                          >
                            Retry
                          </button>
                        </div>
                      )}

                      {turn.run?.status === "running" && (
                        <div className="status-banner status-info" role="status">
                          <div>
                            <strong>Synthesizing build...</strong>
                            <p>
                              Run <code>{turn.run.run_id}</code> is queued or in progress. Updates
                              stream automatically.
                            </p>
                          </div>
                        </div>
                      )}

                      {(turn.run || turn.loading) && (
                        <ProgressStepper
                          run={turn.run}
                          running={Boolean(
                            turn.loading &&
                              turn.run?.status !== "ok" &&
                              turn.run?.status !== "failed" &&
                              turn.run?.status !== "refused" &&
                              turn.run?.status !== "unsupported",
                          )}
                        />
                      )}

                      {turn.run?.status === "ok" && (
                        <>
                          <div className="status-banner status-success" role="status">
                            <div>
                              <strong>
                                {turn.loadingArtifacts
                                  ? "Build ready. Loading files..."
                                  : "Build ready."}
                              </strong>
                              <p>
                                Run <code>{turn.run.run_id}</code> passed the pipeline. Review checks
                                before physical assembly.
                              </p>
                            </div>
                          </div>

                          <div className="chat-assistant-stack">
                            <VerificationBadges
                              verification={turn.trace?.verification ?? null}
                              traceError={turn.traceError}
                            />

                            <PartsPanel
                              spec={turn.spec}
                              catalog={catalog}
                              loading={turn.loadingArtifacts && !turn.spec}
                              onOpenFab={() =>
                                setActiveFab({ runId: turn.run!.run_id, spec: turn.spec })
                              }
                            />

                            <WiringPanel spec={turn.spec} />

                            {canViewStl && (
                              <section
                                className="panel viewer-panel stage-panel"
                                aria-labelledby={`enclosure-heading-${turn.id}`}
                              >
                                <div className="panel-heading">
                                  <div>
                                    <p className="eyebrow">STL preview</p>
                                    <h2 id={`enclosure-heading-${turn.id}`}>Enclosure</h2>
                                  </div>
                                  <span className="stage-tag">Parametric 3D CAD</span>
                                </div>
                                <StlViewer
                                  baseUrl={artifactUrl(turn.run.run_id, "base.stl")}
                                  lidUrl={artifactUrl(turn.run.run_id, "lid.stl")}
                                  assembledUrl={assembledUrl}
                                  theme={theme}
                                />
                              </section>
                            )}

                            {(turn.run.artifacts.simulation_diagram ||
                              turn.run.artifacts.simulation_log ||
                              turn.run.artifacts.simulation_screenshot) && (
                              <div className="stage-panel">
                                <SimulationPanel
                                  run={turn.run}
                                  diagram={turn.wokwiDiagram}
                                  serialLog={turn.serialLog}
                                  loadingDiagram={turn.loadingDiagram}
                                  loadingLog={turn.loadingLog}
                                />
                              </div>
                            )}

                            <FirmwarePanel
                              code={turn.sketch}
                              error={turn.firmwareError}
                              loading={turn.loadingArtifacts && !turn.sketch}
                            />

                            <GuidePanel
                              guide={turn.guide}
                              error={turn.guideError}
                              loading={turn.loadingArtifacts && !turn.guide}
                            />

                            <ValidationPanel
                              issues={validationStage?.issues ?? []}
                              validationStage={validationStage}
                            />

                            {turn.run && <StageFindingsPanel stages={turn.run.stages} />}

                            <DownloadsPanel
                              run={turn.run}
                              onOpenFlasher={() => setActiveFlasherRunId(turn.run!.run_id)}
                            />

                            <OptimizationSuggestions
                              spec={turn.spec}
                              onApplySuggestion={(suggestion) => {
                                setBottomInput(suggestion);
                                bottomInputRef.current?.focus();
                              }}
                            />
                          </div>
                        </>
                      )}

                      {turn.run &&
                        (turn.run.status === "refused" || turn.run.status === "unsupported") && (
                          <RefusalPanel run={turn.run} />
                        )}

                      {turn.run?.status === "failed" && (
                        <FailurePanel run={turn.run} reason={outcomeReason} />
                      )}

                      <ArtifactErrors errors={turn.artifactErrors} />
                    </div>
                  </div>
                </article>
              );
            })}
            <div ref={chatEndRef} />
          </div>
        )}

        <footer className="site-footer">
          <p>
            SparkPlan only recommends parts in its catalog. Every build is software-checked, not
            physically tested.
          </p>
        </footer>
      </main>

      {/* ── STICKY CHATGPT-STYLE BOTTOM PROMPT BAR (SHOWN WHEN THREAD ACTIVE) ── */}
      {turns.length > 0 && (
        <div className="chatgpt-dock" role="region" aria-label="Hardware conversation bar">
          <div className="chatgpt-dock-inner">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!loading && bottomInput.trim()) {
                  void handleSend(bottomInput);
                }
              }}
              className="chatgpt-dock-form"
            >
              <textarea
                ref={bottomInputRef}
                value={bottomInput}
                onChange={(e) => setBottomInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (!loading && bottomInput.trim()) {
                      void handleSend(bottomInput);
                    }
                  }
                }}
                placeholder="e.g. 'Add a passive buzzer that beeps when temp > 30°C' or enter a new hardware idea..."
                rows={1}
                className="chatgpt-dock-input"
                disabled={loading}
              />
              <button
                type="submit"
                className="primary-button chatgpt-dock-send-btn"
                disabled={loading || !bottomInput.trim()}
                aria-label="Send modification"
              >
                {loading ? <span className="spinner-dot" /> : "⚡ Send"}
              </button>
            </form>
            <div className="chatgpt-dock-footnote">
              Grounded in 20-part catalog · Press <code>Enter ↵</code> to send, <code>Shift + Enter</code> for line break
            </div>
          </div>
        </div>
      )}

      {/* ── HARDWARE FLASHER MODAL ── */}
      {activeFlasherRunId && (
        <HardwareFlasher
          runId={activeFlasherRunId}
          isOpen={Boolean(activeFlasherRunId)}
          onClose={() => setActiveFlasherRunId(null)}
        />
      )}

      {/* ── FABRICATION QUOTE MODAL ── */}
      {activeFab && (
        <FabricationModal
          runId={activeFab.runId}
          spec={activeFab.spec}
          catalog={catalog}
          isOpen={Boolean(activeFab)}
          onClose={() => setActiveFab(null)}
        />
      )}
      </div>
    </div>
  );
}
