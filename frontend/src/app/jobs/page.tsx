import type { Metadata } from "next";
import { Suspense } from "react";
import { JobFromQuery } from "@/components/JobFromQuery";
import { JobLoading, ReviewShell } from "@/components/review/ReviewShell";

export const metadata: Metadata = { title: "Проверка записи — Forte" };

export default function JobPage() {
  return <ReviewShell><Suspense fallback={<JobLoading />}><JobFromQuery /></Suspense></ReviewShell>;
}
