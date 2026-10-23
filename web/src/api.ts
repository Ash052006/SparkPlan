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

export type RunRefusal = {
  reasons: string[];
  missing_capabilities: string[];
  closest_supported_alternative: string;
};

export type RunResult = {
  schema_version: string;
  run_id: string;
  status: RunStatus;
  stages: RunStageResult[];
  issues: Issue[];
  artifacts: Record<string, string>;
  refusal?: RunRefusal | null;
  created_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
};

export type SpecPart = {
  instance_id: string;
  part_id: string;
  quantity: number;
  label: string;
};

export type Connection = {
  from_instance: string;
  from_pin: string;
  to_instance: string;
  to_pin: string;
  signal_role: "signal" | "rail" | "gnd" | "sda" | "scl" | "passive" | string;
  rail_name?: string | null;
  note?: string;
};

export type SupportedSpec = {
  status: "ok";
  title: string;
  summary: string;
  parts: SpecPart[];
  connections: Connection[];
  layout?: { part_id: string; mount: string; note?: string }[];
  behaviors?: { name: string; description?: string }[];
};

export type CatalogPart = {
  id: string;
  name: string;
  category: string;
  interface: string;
  mount: string;
  supply_voltage_v: { min: number; max: number };
  typical_current_ma: number;
  peak_current_ma: number;
  pins: string[];
};

export type Catalog = {
  board: {
    id: string;
    name: string;
    rails: {
      name: string;
      voltage_v: number;
      max_current_ma: number;
      source_note?: string;
      approximate?: boolean;
      verify_against_datasheet?: boolean;
    }[];
  };
  parts: CatalogPart[];
  capabilities: string[];
};

export type VerificationValue = boolean | "skipped";

export type Verification = {
  validated: VerificationValue;
  compiled: VerificationValue;
  sliced: VerificationValue;
  simulated: VerificationValue;
};

export type Trace = {
  schema_version: string;
  run_id: string;
  status: RunStatus;
  idea: string;
  verification: Verification;
  validation_report?: {
    passed: boolean;
    issues: Issue[];
    timings_ms?: Record<string, number>;
  };
  issues?: Issue[];
  stages?: RunStageResult[];
};

export type ArtifactName =
  | "spec"
  | "report"
  | "trace"
  | "base_stl"
  | "lid_stl"
  | "assembled_stl"
  | "sketch"
  | "guide";

export type ArtifactLink = {
  name: ArtifactName;
  filename: string;
  label: string;
};

export const MAX_IDEA_LENGTH = 500;

export const STAGE_ORDER = [
  "plan",
  "validation",
  "geometry",
  "slicing",
  "firmware_generation",
  "firmware_compile",
  "guide",
] as const;

export const STAGE_LABELS: Record<(typeof STAGE_ORDER)[number], string> = {
  plan: "Planning",
  validation: "Validation",
  geometry: "Geometry",
  slicing: "Slicing",
  firmware_generation: "Firmware",
  firmware_compile: "Compile",
  guide: "Guide",
};

export const ARTIFACT_LINKS: ArtifactLink[] = [
  { name: "spec", filename: "spec.json", label: "Build spec" },
  { name: "report", filename: "report.json", label: "Run report" },
  { name: "trace", filename: "trace.json", label: "Verification trace" },
  { name: "base_stl", filename: "base.stl", label: "Base STL" },
  { name: "lid_stl", filename: "lid.stl", label: "Lid STL" },
  { name: "assembled_stl", filename: "assembled.stl", label: "Assembled STL" },
  { name: "sketch", filename: "sketch.ino", label: "Firmware" },
  { name: "guide", filename: "GUIDE.md", label: "Build guide" },
];

export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

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
  }
  return text && text.length <= 300 ? text : `HTTP ${response.status}`;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new ApiError(await responseError(response), response.status);
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("The server returned an invalid JSON response.", response.status);
  }
}

export async function startRun(idea: string, signal?: AbortSignal): Promise<string> {
  const body = await requestJson<{ run_id?: unknown }>("/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idea }),
    signal,
  });
  if (typeof body.run_id !== "string" || body.run_id.length === 0) {
    throw new ApiError("The server did not return a run id.");
  }
  return body.run_id;
}

export async function fetchRun(runId: string, signal?: AbortSignal): Promise<RunResult> {
  return requestJson<RunResult>(`/runs/${encodeURIComponent(runId)}`, { signal });
}

function abortError(): DOMException {
  return new DOMException("The request was cancelled.", "AbortError");
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export type WaitForRunOptions = {
  signal?: AbortSignal;
  intervalMs?: number;
  maxPolls?: number;
};

export async function waitForRun(
  runId: string,
  onUpdate?: (result: RunResult) => void,
  options: WaitForRunOptions = {},
): Promise<RunResult> {
  const intervalMs = options.intervalMs ?? 1000;
  const maxPolls = options.maxPolls ?? 600;
  let result = await fetchRun(runId, options.signal);
  onUpdate?.(result);
  for (let poll = 0; result.status === "running" && poll < maxPolls; poll += 1) {
    if (options.signal?.aborted) throw abortError();
    await delay(intervalMs, options.signal);
    result = await fetchRun(runId, options.signal);
    onUpdate?.(result);
  }
  if (result.status === "running") {
    throw new ApiError("The run did not finish within the polling window.");
  }
  return result;
}

export async function fetchArtifactText(
  runId: string,
  filename: string,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(artifactUrl(runId, filename), { signal });
  if (!response.ok) throw new ApiError(await responseError(response), response.status);
  return response.text();
}

export async function fetchArtifactJson<T>(
  runId: string,
  filename: string,
  signal?: AbortSignal,
): Promise<T> {
  const text = await fetchArtifactText(runId, filename, signal);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(`${filename} is not valid JSON.`);
  }
}

export async function fetchTrace(runId: string, signal?: AbortSignal): Promise<Trace> {
  return fetchArtifactJson<Trace>(runId, "trace.json", signal);
}

export async function fetchCatalog(signal?: AbortSignal): Promise<Catalog> {
  return requestJson<Catalog>("/catalog", { signal });
}
