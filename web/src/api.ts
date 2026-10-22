export type Issue = {
  code: string;
  severity: "error" | "warning" | string;
  message: string;
  part_ids?: string[];
  fix_hint?: string;
};

export type StageStatus = "passed" | "failed" | "skipped" | "unverified";
export type RunStatus = "running" | "ok" | "refused" | "unsupported" | "failed";

export type RunStageResult = {
  name: string;
  status: StageStatus;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  issues: Issue[];
  reason?: string | null;
  evidence?: Record<string, string>;
  artifacts?: Record<string, string>;
};

export type RunResult = {
  schema_version: string;
  run_id: string;
  status: RunStatus;
  stages: RunStageResult[];
  issues: Issue[];
  artifacts: Record<string, string>;
  created_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
};

export type SpecPart = {
  instance_id: string;
  part_id: string;
  quantity?: number;
  label?: string;
};

export type SupportedSpec = {
  status?: "ok";
  title: string;
  summary: string;
  parts: SpecPart[];
};

export type ArtifactName =
  | "spec"
  | "base_stl"
  | "lid_stl"
  | "assembled_stl"
  | "sketch"
  | "guide";

export const STAGE_ORDER = [
  "plan",
  "validation",
  "geometry",
  "slicing",
  "firmware_generation",
  "firmware_compile",
  "guide",
] as const;

export const ARTIFACT_LINKS: { name: ArtifactName; filename: string; label: string }[] = [
  { name: "spec", filename: "spec.json", label: "Spec" },
  { name: "base_stl", filename: "base.stl", label: "Base STL" },
  { name: "lid_stl", filename: "lid.stl", label: "Lid STL" },
  { name: "assembled_stl", filename: "assembled.stl", label: "Assembled STL" },
  { name: "sketch", filename: "sketch.ino", label: "Firmware" },
  { name: "guide", filename: "GUIDE.md", label: "Guide" },
];

export function artifactUrl(runId: string, filename: string): string {
  return `/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(filename)}`;
}

export function bundleUrl(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}/bundle.zip`;
}

async function responseError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (body.detail && typeof body.detail === "object" && !Array.isArray(body.detail)) {
      const detail = body.detail as { message?: unknown; code?: unknown };
      if (typeof detail.message === "string") return detail.message;
      if (typeof detail.code === "string") return detail.code;
    }
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: unknown }).msg);
          }
          return "";
        })
        .filter(Boolean);
      if (messages.length > 0) return messages.join("; ");
    }
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Fall through to the HTTP status below.
  }
  return text && text.length <= 300 ? text : `HTTP ${response.status}`;
}

export async function startRun(idea: string): Promise<string> {
  const response = await fetch("/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idea }),
  });
  if (!response.ok) throw new Error(await responseError(response));
  const body = (await response.json()) as { run_id?: unknown };
  if (typeof body.run_id !== "string" || body.run_id.length === 0) {
    throw new Error("The server did not return a run id.");
  }
  return body.run_id;
}

export async function fetchRun(runId: string): Promise<RunResult> {
  const response = await fetch(`/runs/${encodeURIComponent(runId)}`);
  if (!response.ok) throw new Error(await responseError(response));
  return (await response.json()) as RunResult;
}

export async function waitForRun(
  runId: string,
  onUpdate?: (result: RunResult) => void,
): Promise<RunResult> {
  const pollIntervalMs = 1000;
  const maxPolls = 600;
  let result = await fetchRun(runId);
  onUpdate?.(result);
  for (let poll = 0; result.status === "running" && poll < maxPolls; poll += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, pollIntervalMs));
    result = await fetchRun(runId);
    onUpdate?.(result);
  }
  if (result.status === "running") {
    throw new Error("The run did not finish within the polling window.");
  }
  return result;
}

export async function fetchArtifactText(runId: string, filename: string): Promise<string> {
  const response = await fetch(artifactUrl(runId, filename));
  if (!response.ok) throw new Error(await responseError(response));
  return response.text();
}
