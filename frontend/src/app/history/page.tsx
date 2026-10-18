import type { Metadata } from "next";
import Link from "next/link";
import { HistoryList } from "@/components/HistoryList";
import { ReviewHeading, ReviewShell } from "@/components/review/ReviewShell";
import styles from "@/components/review/review.module.css";

export const metadata: Metadata = { title: "История проверок — Forte" };

export default function HistoryPage() {
  return (
    <ReviewShell>
      <ReviewHeading section="Журнал" title="История проверок" description="Все записи и результаты анализа в одном месте.">
        <Link href="/#upload" className={styles.primaryButton}><span aria-hidden="true">+</span> Новая проверка</Link>
      </ReviewHeading>
      <HistoryList />
    </ReviewShell>
  );
}
