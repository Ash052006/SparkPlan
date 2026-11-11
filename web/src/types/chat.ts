import { RunResult, SupportedSpec, Trace } from "../api";
import { WokwiDiagram } from "../components/WokwiDiagramViewer";

export interface ChatTurn {
  id: string;
  prompt: string;
  run: RunResult | null;
  spec: SupportedSpec | null;
  trace: Trace | null;
  sketch: string;
  guide: string;
  wokwiDiagram: WokwiDiagram | null;
  serialLog: string;
  loading: boolean;
  loadingArtifacts: boolean;
  loadingDiagram: boolean;
  loadingLog: boolean;
  traceError: string;
  firmwareError: string;
  guideError: string;
  artifactErrors: string[];
  error: string;
  studioTab?: "all" | "circuit" | "cad" | "firmware" | "bom" | "guide";
  timestamp: Date;
}

export interface ChatSession {
  id: string;
  title: string;
  turns: ChatTurn[];
  createdAt: Date;
}
