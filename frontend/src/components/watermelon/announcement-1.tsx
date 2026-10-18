"use client";

import Link from "next/link";
import { jobHref } from "@/lib/job-links";
import type { JobCompletion } from "@/lib/job-activity";
import styles from "./announcement-1.module.css";

/** Watermelon announcement-1, connected to actual completed analyses. */
export default function Announcement1({ completion, remaining, uploading = false, onDismiss }: {
  completion?: JobCompletion; remaining: number; uploading?: boolean; onDismiss: (id: string) => void;
}) {
  if (!completion) return null;
  return <aside className={styles.announcement} data-uploading={uploading} aria-label="Готовый результат анализа">
    <div className={styles.message} role="status" aria-live="polite" aria-atomic="true">
      <span className={styles.badge}><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m3 8 3 3 7-7" /></svg>Готово</span>
      <p>Анализ завершён <span aria-hidden="true">·</span> <strong title={completion.filename}>{completion.filename}</strong>{remaining > 0 && <span className={styles.count}>Ещё {remaining}</span>}</p>
    </div>
    <div className={styles.actions}>
      {uploading ? <span className={styles.uploadHint}>Результат доступен после отправки</span> : <Link href={jobHref(completion.id)} className={styles.resultLink} onClick={() => onDismiss(completion.id)}>Открыть результат <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M5 15 15 5M5 5h10v10" /></svg></Link>}
      {!uploading && <button type="button" className={styles.close} onClick={() => onDismiss(completion.id)} aria-label={`Скрыть уведомление о ${completion.filename}`}><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg></button>}
    </div>
  </aside>;
}
