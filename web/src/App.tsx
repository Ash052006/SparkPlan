import { FormEvent, useState } from "react";
import StlViewer from "./StlViewer";

type Issue = {
  code: string;
  severity: string;
  message: string;
  fix_hint?: string;
};

type PartInstance = { instance_id: string; part_id: string };

type BuildSummary = {
  id: string;
  status: "ok" | "refused" | "failed";
  refusal_reasons: string[];
  issues: Issue[];
  spec?: { idea: string; parts: PartInstance[]; notes?: string };
  libraries?: string[];
};

export default function App() {
  const [idea, setIdea] = useState("desk weather station showing temperature and humidity");
  const [build, setBuild] = useState<BuildSummary | null>(null);
  const [guide, setGuide] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setBuild(null);
    setGuide("");
    try {
      const resp = await fetch("/v1/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idea }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: BuildSummary = await resp.json();
      setBuild(data);
      if (data.status === "ok") {
        const g = await fetch(`/v1/builds/${data.id}/guide.md`);
        setGuide(await g.text());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

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
          {loading ? "Building..." : "Build"}
        </button>
      </form>

      {error && (
        <div className="panel">
          <span className="badge failed">error</span> {error}
        </div>
      )}

      {build && (
        <div className="panel">
          <span className={`badge ${build.status}`}>{build.status}</span>
          {build.status !== "ok" && (
            <ul>
              {build.refusal_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          {build.spec && (
            <>
              <h3>Parts</h3>
              <ul>
                {build.spec.parts.map((p) => (
                  <li key={p.instance_id}>
                    <code>{p.instance_id}</code> - {p.part_id}
                  </li>
                ))}
              </ul>
            </>
          )}
          {build.libraries && build.libraries.length > 0 && (
            <>
              <h3>Libraries</h3>
              <ul>
                {build.libraries.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {build?.status === "ok" && (
        <div className="grid">
          <div className="panel">
            <h3>Enclosure</h3>
            <StlViewer
              baseUrl={`/v1/builds/${build.id}/enclosure/base.stl`}
              lidUrl={`/v1/builds/${build.id}/enclosure/lid.stl`}
            />
            <p>
              <a href={`/v1/builds/${build.id}/enclosure/base.stl`}>base.stl</a>{" "}
              <a href={`/v1/builds/${build.id}/enclosure/lid.stl`}>lid.stl</a>{" "}
              <a href={`/v1/builds/${build.id}/firmware/sparkplan.ino`}>sparkplan.ino</a>
            </p>
          </div>
          <div className="panel">
            <h3>Build guide</h3>
            <pre>{guide}</pre>
          </div>
        </div>
      )}

      {build && build.issues.length > 0 && (
        <div className="panel">
          <h3>Validator findings</h3>
          <ul>
            {build.issues.map((i, idx) => (
              <li key={idx}>
                <code>{i.code}</code> [{i.severity}] {i.message}
                {i.fix_hint ? ` - ${i.fix_hint}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
