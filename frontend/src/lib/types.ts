/** Shapes returned by the FastAPI interface API (GET /api/jobs/{id}). */

export type JobState = "queued" | "running" | "done" | "failed";

export type RuleRef = {
  rule_id: string;
  document: string;
  provision: string;
  url: string;
  verified: boolean;
};

export type EvidenceFrame = {
  timestamp_sec: number;
  url: string;
};

export type Violation = {
  event_id: string;
  event_type: string;
  type: string;
  timecode_sec: number;
  start_sec: number;
  end_sec: number;
  description: string;
  rules: RuleRef[];
  evidence_frames: EvidenceFrame[];
};

export type AnalysisContext = {
  is_demo?: boolean;
  checked_types?: string[];
  limitations?: string[];
  unassessed_segments?: { start_sec: number; end_sec: number; reason: string }[];
};

type JobMetadata = {
  job_id: string;
  stage: string | null;
  progress: number;
  queue_position: number | null;
  filename: string;
  size_bytes: number;
  duration_sec: number | null;
  /** Unix seconds, as returned by FastAPI. */
  created_at: number;
  finished_at: number | null;
  analysis?: AnalysisContext;
};

export type JobView = JobMetadata & (
  | { state: "queued" | "running"; status: "processing"; error: null }
  | { state: "failed"; status: "failed"; error: string | null }
  | { state: "done"; status: "done"; error: null; verdict: "violation" | "no_violation"; violations: Violation[] }
);
