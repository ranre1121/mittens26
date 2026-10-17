import "server-only";
import { UploadError } from "@/lib/jobs";

/** Turns a thrown UploadError into its JSON response; rethrows anything else. */
export function uploadErrorResponse(err: unknown) {
  if (err instanceof UploadError) {
    return Response.json({ error: err.message }, { status: err.status });
  }
  throw err;
}

export function notFound() {
  return Response.json({ error: "Job not found" }, { status: 404 });
}
