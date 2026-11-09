/**
 * SimulationPanel — shows the full Wokwi simulation output for a completed run.
 *
 * Three tabs:
 *   Circuit — Interactive SVG schematic rendered from diagram.json
 *   Console — Scrollable serial-monitor terminal showing simulation.log
 *   Screenshot — The captured OLED/component screenshot from the scenario
 *
 * Always distinguishes virtual simulation from physical hardware testing.
 */
import { useEffect, useRef, useState } from "react";
import { RunResult, RunStageResult, artifactUrl } from "../api";
import WokwiDiagramViewer, { WokwiDiagram } from "./WokwiDiagramViewer";

type Tab = "circuit" | "console" | "screenshot";

type Props = {
  run: RunResult;
  diagram: WokwiDiagram | null;
  serialLog: string;
  loadingDiagram: boolean;
  loadingLog: boolean;
};

function SimBadge({ stage }: { stage: RunStageResult | undefined }) {
  if (!stage) return null;
  const statusClass =
    stage.status === "passed"
      ? "passed"
      : stage.status === "failed"
        ? "failed"
        : stage.status === "unverified"
          ? "warning"
          : "skipped";
  return <span className={`badge ${statusClass}`}>{stage.status}</span>;
}

function EvidenceRow({ label, value }: { label: string; value: string }) {
  const bool = value === "true" ? "✓" : value === "false" ? "✗" : value;
  const cls =
    value === "true" ? "sim-evidence-ok" : value === "false" ? "sim-evidence-fail" : "";
  return (
    <div className="sim-evidence-row">
      <span className="sim-evidence-label">{label}</span>
      <span className={`sim-evidence-value ${cls}`}>{bool}</span>
    </div>
  );
}

function WireLegend() {
  const wires = [
    { color: "red", label: "Power (3V3/5V)" },
    { color: "black", label: "Ground" },
    { color: "green", label: "GPIO / Signal" },
    { color: "#0066ff", label: "I²C SDA" },
    { color: "#ccaa00", label: "I²C SCL" },
    { color: "gray", label: "Passive" },
  ];
  return (
    <div className="sim-legend">
      {wires.map(({ color, label }) => (
        <span key={label} className="sim-legend-item">
          <span className="sim-legend-swatch" style={{ background: color }} />
          {label}
        </span>
      ))}
    </div>
  );
}

export default function SimulationPanel({
  run,
  diagram,
  serialLog,
  loadingDiagram,
  loadingLog,
}: Props) {
  const [tab, setTab] = useState<Tab>("circuit");
  const consoleRef = useRef<HTMLPreElement>(null);

  const simStage = run.stages.find((s) => s.name === "simulation");
  const evidence = simStage?.evidence ?? {};

  const hasScreenshot = Boolean(run.artifacts.simulation_screenshot);
  const hasDiagram = diagram !== null && !loadingDiagram;
  const hasLog = Boolean(serialLog) && !loadingLog;

  // Auto-scroll terminal to bottom when log updates
  useEffect(() => {
    if (tab === "console" && consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [serialLog, tab]);

  // Parse serial log: strip ANSI-like CR noise, normalise line endings
  const cleanedLog = serialLog
    .replace(/\r\r\n/g, "\n")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .trim();

  const logLines = cleanedLog ? cleanedLog.split("\n") : [];

  // Highlight the boot marker line in the console
  function lineClass(line: string): string {
    if (line.includes("SPARKPLAN_READY")) return "log-line log-line-ready";
    if (line.includes("Scenario completed")) return "log-line log-line-pass";
    if (line.match(/^(ets |rst:|configsip:|clk_drv:|mode:|load:|ho |entry )/))
      return "log-line log-line-boot";
    return "log-line";
  }

  const statusLabel =
    simStage?.status === "passed"
      ? "Virtual simulation passed"
      : simStage?.status === "failed"
        ? "Virtual simulation failed"
        : simStage?.status === "unverified"
          ? "Simulation unverified"
          : "Simulation skipped";

  const projectDownloadUrl = run.artifacts.simulation_project
    ? artifactUrl(run.run_id, "project.zip")
    : null;

  return (
    <section className="panel sim-panel" aria-labelledby="sim-panel-heading">
      {/* Header */}
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Virtual ESP32 · Wokwi</p>
          <h2 id="sim-panel-heading">Simulation</h2>
        </div>
        <div className="sim-header-right">
          <SimBadge stage={simStage} />
          {projectDownloadUrl && (
            <a
              className="ghost-button sim-download-btn"
              href={projectDownloadUrl}
              download="wokwi-project.zip"
              title="Download Wokwi project to open in Wokwi or VS Code"
            >
              ↓ Download project
            </a>
          )}
        </div>
      </div>

      {/* Evidence strip */}
      {simStage && (
        <div className="sim-evidence-strip">
          {evidence.boot_marker_seen !== undefined && (
            <EvidenceRow label="Boot marker" value={evidence.boot_marker_seen} />
          )}
          {evidence.screenshot_verified !== undefined && (
            <EvidenceRow label="Screenshot" value={evidence.screenshot_verified} />
          )}
          {evidence.exit_code !== undefined && (
            <EvidenceRow label="Exit code" value={evidence.exit_code} />
          )}
          {evidence.unsupported_instances !== undefined && (
            <EvidenceRow label="Unsupported parts" value={evidence.unsupported_instances} />
          )}
          <div className="sim-evidence-row">
            <span className="sim-evidence-label">Status</span>
            <span className="sim-evidence-value">{statusLabel}</span>
          </div>
        </div>
      )}

      {/* Disclaimer */}
      <p className="sim-disclaimer">
        <span className="sim-disclaimer-icon">ℹ</span>
        Virtual simulation on a software-emulated ESP32. Physical hardware behaviour may differ.
        No physical device was tested.
      </p>

      {/* Tab bar */}
      <div className="sim-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "circuit"}
          className={`sim-tab ${tab === "circuit" ? "active" : ""}`}
          onClick={() => setTab("circuit")}
        >
          <span className="sim-tab-icon">🔌</span> Circuit
        </button>
        <button
          role="tab"
          aria-selected={tab === "console"}
          className={`sim-tab ${tab === "console" ? "active" : ""}`}
          onClick={() => setTab("console")}
        >
          <span className="sim-tab-icon">📡</span> Serial Monitor
          {hasLog && (
            <span className="sim-tab-badge">{logLines.length}</span>
          )}
        </button>
        <button
          role="tab"
          aria-selected={tab === "screenshot"}
          className={`sim-tab ${tab === "screenshot" ? "active" : ""}`}
          onClick={() => setTab("screenshot")}
          disabled={!hasScreenshot}
        >
          <span className="sim-tab-icon">📸</span> Screenshot
          {!hasScreenshot && <span className="sim-tab-unavail"> (none)</span>}
        </button>
      </div>

      {/* Tab content */}
      <div className="sim-tab-content">
        {/* ── Circuit Diagram ── */}
        {tab === "circuit" && (
          <div className="sim-circuit-wrap">
            {loadingDiagram && (
              <div className="sim-loading">Loading circuit diagram…</div>
            )}
            {!loadingDiagram && !diagram && (
              <div className="sim-empty">
                No circuit diagram is available for this run.
              </div>
            )}
            {hasDiagram && diagram && (
              <>
                <WireLegend />
                <div className="sim-diagram-canvas">
                  <WokwiDiagramViewer diagram={diagram} />
                </div>
                <p className="sim-canvas-hint">
                  Hover a component to highlight its connections.
                  {diagram.parts.length > 0 && (
                    <> {diagram.parts.length} components · {diagram.connections.length} wires.</>
                  )}
                </p>
                <div className="sim-part-list">
                  {diagram.parts.map((p) => (
                    <span key={p.id} className="sim-part-chip">
                      <code>{p.id}</code>
                      <span className="sim-part-type">{p.type.replace(/^wokwi-|^board-/, "")}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* ── Serial Monitor ── */}
        {tab === "console" && (
          <div className="sim-console-wrap">
            <div className="sim-console-toolbar">
              <span className="sim-console-label">
                Serial Monitor · ESP32 UART0 · 115200 baud
              </span>
              <span className="sim-console-lines">{logLines.length} lines</span>
            </div>
            {loadingLog && <div className="sim-loading">Loading serial output…</div>}
            {!loadingLog && !cleanedLog && (
              <div className="sim-empty">No serial log is available for this run.</div>
            )}
            {cleanedLog && (
              <pre ref={consoleRef} className="sim-console" aria-label="ESP32 serial output">
                {logLines.map((line, i) => (
                  <span key={i} className={lineClass(line)}>
                    <span className="log-lineno">{String(i + 1).padStart(3, " ")}</span>
                    {" "}
                    {line}
                    {"\n"}
                  </span>
                ))}
              </pre>
            )}
          </div>
        )}

        {/* ── Screenshot ── */}
        {tab === "screenshot" && (
          <div className="sim-screenshot-wrap">
            {!hasScreenshot && (
              <div className="sim-empty">
                No screenshot was captured. The simulation scenario may not have included a
                display component, or the scenario did not complete.
              </div>
            )}
            {hasScreenshot && (
              <>
                <div className="sim-screenshot-frame">
                  <img
                    src={artifactUrl(run.run_id, "screenshot.png")}
                    alt="Captured Wokwi simulation screenshot"
                    className="sim-screenshot-img"
                  />
                </div>
                <p className="sim-canvas-hint">
                  Screenshot captured by wokwi-cli after the boot marker was seen.
                  This shows the state of the virtual display at that moment.
                </p>
                <a
                  href={artifactUrl(run.run_id, "screenshot.png")}
                  download="simulation-screenshot.png"
                  className="ghost-button sim-dl-img"
                >
                  ↓ Download screenshot
                </a>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
