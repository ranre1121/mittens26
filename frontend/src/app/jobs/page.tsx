"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { JobView } from "@/components/JobView";

// A static export can't pre-render one page per job, so the job id travels in
// the query string: /jobs/?id=<job_id>.
function JobFromQuery() {
  const id = useSearchParams().get("id");
  if (!id) {
    return <p className="text-muted">Не указан идентификатор проверки.</p>;
  }
  return <JobView key={id} id={id} />;
}

export default function JobPage() {
  return (
    <Suspense fallback={<p className="text-muted">Загрузка…</p>}>
      <JobFromQuery />
    </Suspense>
  );
}
