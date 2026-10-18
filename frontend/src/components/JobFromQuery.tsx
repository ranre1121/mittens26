"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { JobView } from "@/components/JobView";
import { jobHref } from "@/lib/job-links";
import styles from "@/components/review/review.module.css";

// Job ids are created at runtime; static exports therefore use /jobs/?id=… .
export function JobFromQuery() {
  const query = useSearchParams();
  const id = query.get("id");
  if (!id || !/^[a-f0-9]{12}$/.test(id)) {
    return <section className={styles.notice}><h1>Проверка не найдена</h1><p>Выберите запись в истории проверок.</p><Link href="/history" className={styles.primaryButton}>История проверок</Link></section>;
  }
  const batch = [...new Set((query.get("batch") ?? "").split(",").filter((value) => /^[a-f0-9]{12}$/.test(value)))];
  return <>
    {batch.length > 1 && batch.includes(id) && <nav className={styles.batchNav} aria-label="Загруженные записи">
      <p>Записи в проверке</p>
      <ul>{batch.map((jobId, index) => <li key={jobId}><Link className={styles.batchAction} href={jobHref(jobId, batch)} aria-current={jobId === id ? "page" : undefined}>Запись {index + 1}</Link></li>)}</ul>
    </nav>}
    <JobView key={id} id={id} />
  </>;
}
