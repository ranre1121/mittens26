import Link from "next/link";
import type { ReactNode } from "react";
import styles from "./review.module.css";

export function ReviewShell({ children }: { children: ReactNode }) {
  return <div className={styles.workspace}>{children}</div>;
}

export function ReviewHeading({ section, title, description, back, children }: {
  section: string;
  title: string;
  description: ReactNode;
  back?: boolean;
  children?: ReactNode;
}) {
  return (
    <header className={styles.heading}>
      <div className={styles.headingContent}>
        {back ? <Link href="/history" className={styles.backLink}><span aria-hidden="true">←</span> История проверок</Link> : <p className={styles.eyebrow}>Внутренний контроль / {section}</p>}
        <h1 title={title}>{title}</h1>
        <div className={styles.headingDescription}>{description}</div>
        {children}
      </div>
      <div className={styles.wordmark} aria-hidden="true" />
    </header>
  );
}

export function RecordIcon({ className }: { className?: string }) {
  return (
    <svg className={className} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="3" /><path d="m10 9 5 3-5 3V9Z" />
    </svg>
  );
}

export function CheckIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="m6 12 4 4 8-8" /></svg>;
}

export function JobLoading() {
  return (
    <div className={styles.loadingPage} role="status">
      <div className={styles.loadingOrbit} aria-hidden="true"><span /></div>
      <h1>Открываем проверку</h1>
      <p>Загружаем данные записи и статус анализа…</p>
    </div>
  );
}
