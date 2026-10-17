import { z } from "zod";

/**
 * What the analyzer posts back for a finished job. The shape matches the jury
 * API (section 10 of the case), plus an optional `rule` that the UI shows as
 * the regulatory reference (section 4).
 */
export const evidenceFrameSchema = z.object({
  timestamp_sec: z.number().nonnegative(),
  // Raw base64 JPEG, no `data:` prefix.
  image_base64: z.string().min(1),
});

export const ruleSchema = z.object({
  document: z.string().min(1),
  clause: z.string().optional(),
  url: z.string().url().optional(),
});

export const violationSchema = z.object({
  type: z.string().min(1),
  timecode_sec: z.number().nonnegative(),
  description: z.string().min(1),
  evidence_frames: z.array(evidenceFrameSchema).min(1).max(3),
  rule: ruleSchema.optional(),
});

export const analysisResultSchema = z.object({
  violations: z.array(violationSchema),
});

export type EvidenceFrame = z.infer<typeof evidenceFrameSchema>;
export type Rule = z.infer<typeof ruleSchema>;
export type Violation = z.infer<typeof violationSchema>;
export type AnalysisResult = z.infer<typeof analysisResultSchema>;

export type Verdict = "violation" | "no_violation";

/** Body of GET /api/v1/jobs/{id}. */
export type PublicJob =
  | { job_id: string; status: "processing" }
  | { job_id: string; status: "failed"; error: string }
  | {
      job_id: string;
      status: "done";
      verdict: Verdict;
      violations: Violation[];
    };

/** Body of GET /api/jobs/{id}: the public shape plus what the UI needs. */
export type JobView = PublicJob & {
  stage: "queued" | "processing" | "done" | "failed";
  filename: string;
  size_bytes: number;
  progress: number;
  created_at: number;
  finished_at: number | null;
};
