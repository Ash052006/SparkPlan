import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ARTIFACT_LINKS,
  Catalog,
  Connection,
  Issue,
  RunRefusal,
  RunResult,
  RunStageResult,
  STAGE_LABELS,
  SupportedSpec,
  artifactUrl,
  bundleUrl,
} from "../api";

function titleCase(value: string): string {
  if (!value) return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function issueKey(issue: Issue, index: number): string {
  return `${issue.code}-${issue.severity}-${index}-${issue.message}`;
}

export function IssueList({ issues, empty }: { issues: Issue[]; empty?: string }) {
  if (issues.length === 0) {
    return empty ? <p className="muted">{empty}</p> : null;
  }
  return (
    <ul className="issue-list">
      {issues.map((issue, index) => (
        <li key={issueKey(issue, index)} className={`issue issue-${issue.severity}`}>
          <div className="issue-heading">
            <span className={`badge ${issue.severity === "error" ? "failed" : "warning"}`}>
              {issue.severity}
            </span>
            <code>{issue.code}</code>
          </div>
          <p>{issue.message}</p>
          {issue.part_ids && issue.part_ids.length > 0 && (
            <p className="issue-meta">Parts: {issue.part_ids.join(", ")}</p>
          )}
          {issue.fix_hint && <p className="issue-meta">Fix: {issue.fix_hint}</p>}
        </li>
      ))}
    </ul>
  );
}

type PartsProps = {
  spec: SupportedSpec | null;
  catalog: Catalog | null;
  loading: boolean;
};

export function PartsPanel({ spec, catalog, loading }: PartsProps) {
  const names = new Map(catalog?.parts.map((part) => [part.id, part]) ?? []);
  return (
    <section className="panel" aria-labelledby="parts-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Bill of materials</p>
          <h2 id="parts-heading">Parts</h2>
        </div>
        {spec && <span className="muted">{spec.parts.length} lines</span>}
      </div>
      {loading && <p className="muted">Loading the validated parts list...</p>}
      {!loading && !spec && <p className="muted">No parts list is available.</p>}
      {spec && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Validated parts list</caption>
            <thead>
              <tr>
                <th scope="col">Qty</th>
                <th scope="col">Instance</th>
                <th scope="col">Part</th>
                <th scope="col">Type</th>
              </tr>
            </thead>
            <tbody>
              {spec.parts.map((part) => {
                const catalogPart = names.get(part.part_id);
                return (
                  <tr key={part.instance_id}>
                    <td>{part.quantity ?? 1}</td>
                    <td>
                      <code>{part.instance_id}</code>
                      {part.label && <span className="cell-note">{part.label}</span>}
                    </td>
                    <td>
                      <span className="part-name">{catalogPart?.name ?? part.part_id}</span>
                      <span className="cell-note">{part.part_id}</span>
                    </td>
                    <td>
                      <span>{titleCase(catalogPart?.category ?? "catalog part")}</span>
                      {catalogPart && <span className="cell-note">{catalogPart.interface}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!catalog && spec && (
        <p className="hint">Catalog names are unavailable. Part IDs are shown instead.</p>
      )}
    </section>
  );
}

function connectionEndpoint(instanceId: string, pin: string, labels: Map<string, string>): string {
  return `${labels.get(instanceId) ?? instanceId} · ${pin}`;
}

export function WiringPanel({ spec }: { spec: SupportedSpec | null }) {
  const labels = new Map(spec?.parts.map((part) => [part.instance_id, part.label || part.instance_id]) ?? []);
  return (
    <section className="panel" aria-labelledby="wiring-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Connections</p>
          <h2 id="wiring-heading">Wiring</h2>
        </div>
        {spec && <span className="muted">{spec.connections.length} connections</span>}
      </div>
      {!spec && <p className="muted">No wiring data is available.</p>}
      {spec && spec.connections.length === 0 && <p className="muted">No connections were declared.</p>}
      {spec && spec.connections.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Validated wiring connections</caption>
            <thead>
              <tr>
                <th scope="col">From</th>
                <th scope="col">To</th>
                <th scope="col">Role</th>
              </tr>
            </thead>
            <tbody>
              {spec.connections.map((connection: Connection, index: number) => (
                <tr key={`${connection.from_instance}-${connection.from_pin}-${index}`}>
                  <td>{connectionEndpoint(connection.from_instance, connection.from_pin, labels)}</td>
                  <td>{connectionEndpoint(connection.to_instance, connection.to_pin, labels)}</td>
                  <td>
                    <span>{titleCase(connection.signal_role)}</span>
                    {connection.rail_name && <span className="cell-note">{connection.rail_name}</span>}
                    {connection.note && <span className="cell-note">{connection.note}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type ValidationProps = {
  issues: Issue[];
  validationStage: RunStageResult | null;
};

export function ValidationPanel({ issues, validationStage }: ValidationProps) {
  return (
    <section className="panel" aria-labelledby="validation-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Deterministic checks</p>
          <h2 id="validation-heading">Validation and findings</h2>
        </div>
        {validationStage && <span className={`badge ${validationStage.status}`}>{validationStage.status}</span>}
      </div>
      <IssueList
        issues={issues}
        empty={validationStage?.status === "passed" ? "No validation issues were reported." : "No issue details are available."}
      />
      {validationStage?.reason && validationStage.status !== "passed" && (
        <p className="reason">{validationStage.reason}</p>
      )}
    </section>
  );
}

export function StageFindingsPanel({ stages }: { stages: RunStageResult[] }) {
  const groups = stages.filter((stage) => stage.name !== "validation" && stage.issues.length > 0);
  if (groups.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="stage-findings-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Tool and stage checks</p>
          <h2 id="stage-findings-heading">Stage findings</h2>
        </div>
        <span className="muted">
          {groups.reduce((total, stage) => total + stage.issues.length, 0)} findings
        </span>
      </div>
      {groups.map((stage) => (
        <div key={stage.name} className="stage-finding-group">
          <h3>{STAGE_LABELS[stage.name as keyof typeof STAGE_LABELS] ?? titleCase(stage.name)}</h3>
          {stage.reason && stage.issues.length === 0 && <p className="muted">{stage.reason}</p>}
          <IssueList issues={stage.issues} />
        </div>
      ))}
    </section>
  );
}

type FirmwareProps = {
  code: string;
  error: string;
  loading: boolean;
};

export function FirmwarePanel({ code, error, loading }: FirmwareProps) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setCopyError("");
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setCopyError("Copy failed. Select the code and copy it manually.");
    }
  }

  return (
    <section className="panel" aria-labelledby="firmware-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Generated template</p>
          <h2 id="firmware-heading">Firmware</h2>
        </div>
        <button type="button" className="ghost-button" onClick={copy} disabled={!code || loading}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {loading && <p className="muted">Loading firmware...</p>}
      {error && <p className="inline-error">{error}</p>}
      {!loading && !error && !code && <p className="muted">No firmware source is available.</p>}
      {code && (
        <pre className="code-block" tabIndex={0} aria-label="Generated firmware source">
          <code>{code}</code>
        </pre>
      )}
      {copyError && <p className="inline-error">{copyError}</p>}
      <p className="hint">Read-only output from the catalog firmware templates.</p>
    </section>
  );
}

type GuideProps = {
  guide: string;
  error: string;
  loading: boolean;
};

export function GuidePanel({ guide, error, loading }: GuideProps) {
  return (
    <section className="panel" aria-labelledby="guide-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Assembly</p>
          <h2 id="guide-heading">Build guide</h2>
        </div>
      </div>
      {loading && <p className="muted">Loading the build guide...</p>}
      {error && <p className="inline-error">{error}</p>}
      {!loading && !error && !guide && <p className="muted">No build guide is available.</p>}
      {guide && (
        <div className="markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{guide}</ReactMarkdown>
        </div>
      )}
    </section>
  );
}

export function DownloadsPanel({ run }: { run: RunResult }) {
  const links = ARTIFACT_LINKS.filter((artifact) => Boolean(run.artifacts[artifact.name]));
  if (links.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="downloads-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Files</p>
          <h2 id="downloads-heading">Downloads</h2>
        </div>
        <a className="primary-button download-bundle" href={bundleUrl(run.run_id)} download>
          Download bundle
        </a>
      </div>
      <ul className="download-list">
        {links.map((artifact) => (
          <li key={artifact.name}>
            <a href={artifactUrl(run.run_id, artifact.filename)} download={artifact.filename}>
              {artifact.filename}
            </a>
            <span className="muted">{artifact.label}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

type RefusalProps = {
  run: RunResult;
};

export function RefusalPanel({ run }: RefusalProps) {
  const refusal: RunRefusal | null = run.refusal ?? null;
  const planStage = run.stages.find((stage) => stage.name === "plan");
  const fallbackReason = planStage?.reason ?? "";
  const reasons = refusal?.reasons ?? (fallbackReason ? [fallbackReason] : []);
  const heading = run.status === "refused" ? "Idea refused" : "Unsupported idea";

  return (
    <section className="panel refusal-panel" aria-labelledby="refusal-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">No build was generated</p>
          <h2 id="refusal-heading">{heading}</h2>
        </div>
        <span className="badge unsupported">{run.status}</span>
      </div>

      <div className="refusal-section">
        <h3>Reasons</h3>
        {reasons.length > 0 ? (
          <ul>
            {reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">The planner did not report a reason.</p>
        )}
      </div>

      <div className="refusal-section">
        <h3>Missing capabilities</h3>
        {refusal && refusal.missing_capabilities.length > 0 ? (
          <ul className="capability-list">
            {refusal.missing_capabilities.map((capability) => (
              <li key={capability}>
                <code>{capability}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No missing capabilities were reported.</p>
        )}
      </div>

      <div className="refusal-section">
        <h3>Closest supported alternative</h3>
        <p>
          {refusal?.closest_supported_alternative || "The planner did not suggest an alternative."}
        </p>
      </div>

      <p className="hint">SparkPlan refuses ideas outside its catalog instead of approximating them.</p>
    </section>
  );
}

type FailureProps = {
  run: RunResult;
  reason: string;
};

export function FailurePanel({ run, reason }: FailureProps) {
  return (
    <section className="panel" aria-labelledby="failure-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Run did not complete</p>
          <h2 id="failure-heading">Build failed</h2>
        </div>
        <span className="badge failed">{run.status}</span>
      </div>
      {reason && <p className="reason">{reason}</p>}
      <IssueList issues={run.issues} empty="No failure details were reported." />
      <p className="hint">No successful build artifacts are available for this run.</p>
    </section>
  );
}

export function ArtifactErrors({ errors }: { errors: string[] }) {
  if (errors.length === 0) return null;
  return (
    <section className="panel artifact-warnings" aria-labelledby="artifact-errors-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Partial results</p>
          <h2 id="artifact-errors-heading">Some files could not be loaded</h2>
        </div>
      </div>
      <ul>
        {errors.map((error) => (
          <li key={error}>{error}</li>
        ))}
      </ul>
      <p className="hint">The run itself may still be valid. Check the server logs and try again.</p>
    </section>
  );
}
