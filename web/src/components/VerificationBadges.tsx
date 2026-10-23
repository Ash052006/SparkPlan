import { Verification, VerificationValue } from "../api";

type Props = {
  verification: Verification | null;
  traceError: string;
};

const CHECKS: { key: keyof Verification; label: string }[] = [
  { key: "validated", label: "Validated" },
  { key: "compiled", label: "Compiled" },
  { key: "sliced", label: "Sliced" },
  { key: "simulated", label: "Simulated" },
];

function valueState(value: VerificationValue | undefined): {
  className: string;
  label: string;
} {
  if (value === true) return { className: "passed", label: "Passed" };
  if (value === false) return { className: "failed", label: "Failed" };
  if (value === "skipped") return { className: "skipped", label: "Skipped" };
  return { className: "unknown", label: "Unknown" };
}

export default function VerificationBadges({ verification, traceError }: Props) {
  return (
    <section className="panel verification-panel" aria-labelledby="verification-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Honest checks</p>
          <h2 id="verification-heading">Verification</h2>
        </div>
        <span className="not-built">Not physically built</span>
      </div>

      <div className="verification-grid">
        {CHECKS.map((check) => {
          const state = valueState(verification?.[check.key]);
          return (
            <div key={check.key} className="verification-item">
              <span className="verification-label">{check.label}</span>
              <span className={`badge ${state.className}`}>{state.label}</span>
            </div>
          );
        })}
      </div>

      {traceError && <p className="inline-error">Verification data unavailable: {traceError}</p>}
      {!verification && !traceError && (
        <p className="muted">Verification data is not available for this run.</p>
      )}
      <p className="hint">
        These checks describe software and tool results only. No enclosure has been printed, wired,
        or tested on hardware.
      </p>
    </section>
  );
}
