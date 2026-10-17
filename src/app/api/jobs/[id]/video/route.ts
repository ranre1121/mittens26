import { getEnv } from "@/lib/env";
import { notFound } from "@/lib/http";
import { getJob } from "@/lib/jobs";

/**
 * Streams the uploaded video from R2, honouring Range requests so the player
 * can seek to a violation's timecode. The analyzer downloads it from here too.
 */
export async function GET(
  request: Request,
  ctx: RouteContext<"/api/jobs/[id]/video">,
) {
  const { id } = await ctx.params;
  const row = await getJob(id);
  if (!row) return notFound();

  const range = request.headers.get("range");
  const obj = await getEnv().MEDIA.get(row.videoKey, {
    range: range ? request.headers : undefined,
  });
  if (!obj) return notFound();

  const headers = new Headers({
    // Browsers decline to play "video/quicktime" even when the codec is H.264,
    // which is what .mov files from phones and CCTV exports usually carry.
    "content-type":
      row.contentType === "video/quicktime" ? "video/mp4" : row.contentType,
    "accept-ranges": "bytes",
    "cache-control": "private, max-age=3600",
    etag: obj.httpEtag,
  });

  const r = obj.range as { offset?: number; length?: number } | undefined;
  if (range && r && r.offset !== undefined) {
    const length = r.length ?? obj.size - r.offset;
    headers.set(
      "content-range",
      `bytes ${r.offset}-${r.offset + length - 1}/${obj.size}`,
    );
    headers.set("content-length", String(length));
    return new Response(obj.body, { status: 206, headers });
  }

  headers.set("content-length", String(obj.size));
  return new Response(obj.body, { headers });
}
