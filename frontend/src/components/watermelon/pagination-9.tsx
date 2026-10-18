"use client";

import styles from "./pagination-9.module.css";

/** Watermelon pagination-9: square targets and an underline on the active page. */
export default function Pagination9({ page, pageCount, pending = false, onPageChange }: {
  page: number;
  pageCount: number;
  pending?: boolean;
  onPageChange: (page: number) => void;
}) {
  const start = Math.max(1, Math.min(page - 1, pageCount - 2));
  const nearby = Array.from({ length: Math.min(3, pageCount) }, (_, index) => start + index);
  const numbers = [...new Set([1, ...nearby, pageCount])].sort((a, b) => a - b);

  return <nav className={styles.pagination} aria-label="Страницы истории">
    <span className="sr-only" aria-live="polite">Страница {page} из {pageCount}</span>
    <ul>
      <li><button type="button" className={styles.direction} disabled={pending || page <= 1} onClick={() => onPageChange(page - 1)} aria-label="Предыдущая страница"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m12 5-5 5 5 5" /></svg><span>Назад</span></button></li>
      {numbers.map((number, index) => <PageItem key={number} number={number} current={page} pending={pending} edge={!nearby.includes(number)} gap={index > 0 && number - numbers[index - 1] > 1} onPageChange={onPageChange} />)}
      <li><button type="button" className={styles.direction} disabled={pending || page >= pageCount} onClick={() => onPageChange(page + 1)} aria-label="Следующая страница"><span>Далее</span><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m8 5 5 5-5 5" /></svg></button></li>
    </ul>
  </nav>;
}

function PageItem({ number, current, pending, edge, gap, onPageChange }: {
  number: number; current: number; pending: boolean; edge: boolean; gap: boolean; onPageChange: (page: number) => void;
}) {
  return <>
    {gap && <li className={styles.ellipsis} aria-hidden="true">…</li>}
    <li className={edge ? styles.edge : undefined}><button type="button" className={styles.page} aria-label={`Страница ${number}`} aria-current={number === current ? "page" : undefined} disabled={pending} onClick={() => onPageChange(number)}>{number}</button></li>
  </>;
}
