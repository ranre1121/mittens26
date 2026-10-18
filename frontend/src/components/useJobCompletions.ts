"use client";

import { useEffect, useRef, useState } from "react";
import type { JobActivity, JobCompletion } from "@/lib/job-activity";
import { apiFetch } from "@/lib/session";

/** Runs once in the persistent layout, including during the upload transition. */
export function useJobCompletions(currentJobId: string | null) {
  const [pending, setPending] = useState<JobCompletion[]>([]);
  const jobRef = useRef(currentJobId);
  useEffect(() => { jobRef.current = currentJobId; }, [currentJobId]);

  useEffect(() => {
    let disposed = false;
    let cursor: string | null = null;
    let request: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout>;
    const seen = new Set<string>();

    async function poll() {
      clearTimeout(timer);
      if (disposed || request || document.visibilityState === "hidden") return;
      request = new AbortController();
      let delay = 5000;
      try {
        const query = cursor === null ? "" : `?${new URLSearchParams({ after: cursor })}`;
        const response = await apiFetch(`/api/jobs/activity${query}`, { cache: "no-store", signal: request.signal });
        if (!response.ok) throw new Error("Activity unavailable");
        const activity = await response.json() as JobActivity;
        if (disposed) return;
        cursor = activity.cursor;
        const incoming = activity.items.filter((item) => {
          if (seen.has(item.id)) return false;
          seen.add(item.id);
          return jobRef.current !== item.id;
        });
        if (incoming.length) setPending((current) => [...current.filter((item) => jobRef.current !== item.id), ...incoming]);
        if (activity.hasMore) delay = 150;
      } catch {
        // Keep the cursor and visible announcements; recover on the next poll.
        delay = 10000;
      } finally {
        request = null;
        if (!disposed) timer = setTimeout(poll, delay);
      }
    }

    void poll();
    window.addEventListener("focus", poll);
    document.addEventListener("visibilitychange", poll);
    return () => {
      disposed = true;
      clearTimeout(timer);
      request?.abort();
      window.removeEventListener("focus", poll);
      document.removeEventListener("visibilitychange", poll);
    };
  }, []);

  const visible = pending.filter((item) => currentJobId !== item.id);
  return {
    completion: visible[0],
    remaining: Math.max(0, visible.length - 1),
    dismiss: (id: string) => setPending((current) => current.filter((item) => item.id !== id)),
  };
}
