import type { Metadata } from "next";
import { JobView } from "@/components/JobView";

export const metadata: Metadata = { title: "Результат проверки" };

export default async function JobPage({ params }: PageProps<"/jobs/[id]">) {
  const { id } = await params;
  return <JobView id={id} />;
}
