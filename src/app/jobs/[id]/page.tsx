import type { Metadata } from "next";
import Link from "next/link";
import { JobView } from "@/components/JobView";
import { jobHref } from "@/lib/job-links";
import { ReviewShell } from "@/components/review/ReviewShell";
import styles from "@/components/review/review.module.css";

export const metadata: Metadata = { title: "Проверка записи — Forte" };

export default async function JobPage({ params, searchParams }: PageProps<"/jobs/[id]">) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  const batch = typeof query.batch === "string"
    ? [...new Set(query.batch.split(",").filter((value) => /^[a-f0-9]{12}$/.test(value)))]
    : [];
  return (
    <ReviewShell>
      {batch.length > 1 && batch.includes(id) && (
        <nav className={styles.batchNav} aria-label="Загруженные записи">
          <p>Записи в проверке</p>
          <ul>{batch.map((jobId, index) => (
            <li key={jobId}><Link className={styles.batchAction} href={jobHref(jobId, batch)} aria-current={jobId === id ? "page" : undefined}>Запись {index + 1}</Link></li>
          ))}</ul>
        </nav>
      )}
      <JobView key={id} id={id} />
    </ReviewShell>
  );
}
