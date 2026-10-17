"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { EvidenceFrame, JobView as Job, Violation } from "@/lib/types";
import { formatBytes, formatTimecode } from "@/lib/format";

const POLL_MS = 2000;

const STAGE_LABELS: Record<string, string> = {
  claimed: "Подготовка",
  probing: "Чтение видео",
  detecting: "Поиск событий",
  evidence: "Подбор кадров-доказательств",
  saving: "Сохранение результата",
};

type Load =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ready"; job: Job };

function useJob(id: string): Load {
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const res = await fetch(`/api/jobs/${id}`, { cache: "no-store" });
        if (cancelled) return;
        if (res.status === 404) return setLoad({ kind: "missing" });
        if (!res.ok) throw new Error(`Ошибка сервера (${res.status})`);
        const job = (await res.json()) as Job;
        if (cancelled) return;
        setLoad({ kind: "ready", job });
        if (job.status === "processing") timer = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (cancelled) return;
        // Keep showing the last good state; just retry a little slower.
        setLoad((prev) =>
          prev.kind === "ready"
            ? prev
            : { kind: "error", message: (err as Error).message },
        );
        timer = setTimeout(poll, POLL_MS * 2);
      }
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id]);

  return load;
}

export function JobView({ id }: { id: string }) {
  const load = useJob(id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [zoomed, setZoomed] = useState<EvidenceFrame | null>(null);

  function seek(sec: number) {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = sec;
    v.pause();
    v.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  if (load.kind === "loading") {
    return <p className="text-muted">Загрузка…</p>;
  }
  if (load.kind === "missing") {
    return (
      <Notice title="Проверка не найдена">
        Проверьте ссылку или <Link href="/" className="text-accent underline">загрузите видео заново</Link>.
      </Notice>
    );
  }
  if (load.kind === "error") {
    return <Notice title="Не удалось получить статус">{load.message}</Notice>;
  }

  const { job } = load;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted">Проверка {job.job_id}</p>
          <h1 className="truncate text-xl font-semibold">{job.filename}</h1>
          <p className="text-sm text-muted">{formatBytes(job.size_bytes)}</p>
        </div>
        <Link
          href="/"
          className="rounded-lg border border-border bg-surface px-4 py-2 text-sm hover:bg-surface-muted"
        >
          Проверить другое видео
        </Link>
      </div>

      <StatusBanner job={job} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-6 lg:self-start">
          <video
            ref={videoRef}
            src={`/api/jobs/${job.job_id}/video`}
            controls
            playsInline
            preload="metadata"
            className="aspect-video w-full rounded-xl border border-border bg-black"
          />
        </div>

        {job.status === "done" && job.violations && job.violations.length > 0 && (
          <ol className="space-y-4">
            {job.violations.map((v, i) => (
              <ViolationCard
                key={i}
                index={i + 1}
                violation={v}
                onSeek={seek}
                onZoom={setZoomed}
              />
            ))}
          </ol>
        )}
      </div>

      {zoomed && (
        <div
          role="dialog"
          aria-modal
          aria-label="Кадр-доказательство"
          onClick={() => setZoomed(null)}
          className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4"
        >
          <figure className="max-w-5xl">
            {/* eslint-disable-next-line @next/next/no-img-element -- frame served by the API */}
            <img
              src={zoomed.url}
              alt={`Кадр на ${formatTimecode(zoomed.timestamp_sec)}`}
              className="max-h-[85vh] w-auto rounded-lg"
            />
            <figcaption className="mt-2 text-center font-mono text-sm text-white">
              {formatTimecode(zoomed.timestamp_sec)} · нажмите, чтобы закрыть
            </figcaption>
          </figure>
        </div>
      )}
    </div>
  );
}

function StatusBanner({ job }: { job: Job }) {
  if (job.status === "processing") {
    const queued = job.state === "queued";
    const stage = job.stage ? STAGE_LABELS[job.stage] : undefined;
    return (
      <div className="rounded-xl border border-border bg-surface p-5">
        <div className="flex items-center gap-3">
          <span className="size-2.5 animate-pulse rounded-full bg-accent" />
          <p className="font-medium">
            {queued ? "В очереди на анализ" : "Идёт анализ видео"}
          </p>
          {queued && job.queue_position ? (
            <span className="text-sm text-muted">
              перед вами: {job.queue_position}
            </span>
          ) : null}
          {!queued && stage && (
            <span className="text-sm text-muted">· {stage}</span>
          )}
          {!queued && (
            <span className="ml-auto font-mono text-sm text-muted">
              {job.progress}%
            </span>
          )}
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface-muted">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-500"
            style={{ width: `${queued ? 2 : Math.max(job.progress, 4)}%` }}
          />
        </div>
        <p className="mt-3 text-sm text-muted">
          Страницу можно не обновлять — результат появится автоматически.
        </p>
      </div>
    );
  }

  if (job.status === "failed") {
    return (
      <Notice title="Не удалось обработать видео" tone="danger">
        {job.error}
      </Notice>
    );
  }

  const found = job.violations?.length ?? 0;
  return found === 0 ? (
    <div className="rounded-xl border border-success/30 bg-success-soft p-5">
      <p className="text-lg font-semibold text-success">
        Нарушение не обнаружено
      </p>
      <p className="mt-1 text-sm text-muted">
        На записи не найдено визуальных признаков нарушений из числа
        распознаваемых сервисом.
      </p>
    </div>
  ) : (
    <div className="rounded-xl border border-danger/30 bg-danger-soft p-5">
      <p className="text-lg font-semibold text-danger">
        Обнаружены признаки возможных нарушений
      </p>
      <p className="mt-1 text-sm text-muted">
        Найдено событий: {found}. Проверьте каждый фрагмент перед выводами.
      </p>
    </div>
  );
}

function ViolationCard({
  index,
  violation: v,
  onSeek,
  onZoom,
}: {
  index: number;
  violation: Violation;
  onSeek: (sec: number) => void;
  onZoom: (frame: EvidenceFrame) => void;
}) {
  return (
    <li className="rounded-xl border border-border bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="font-medium">
          <span className="text-muted">{index}.</span> {v.type}
        </h2>
        <button
          type="button"
          onClick={() => onSeek(v.timecode_sec)}
          className="shrink-0 rounded-md bg-accent-soft px-2 py-1 font-mono text-sm text-accent hover:opacity-80"
          title="Перейти к моменту на видео"
        >
          ▶ {formatTimecode(v.timecode_sec)}
        </button>
      </div>

      <p className="mt-2 text-sm leading-relaxed">{v.description}</p>

      <div className="mt-4 grid grid-cols-3 gap-2">
        {v.evidence_frames.map((f, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onZoom(f)}
            className="group relative overflow-hidden rounded-lg border border-border"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- frame served by the API */}
            <img
              src={f.url}
              alt={`Кадр на ${formatTimecode(f.timestamp_sec)}`}
              className="aspect-video w-full object-cover transition-transform group-hover:scale-105"
            />
            <span className="absolute bottom-1 left-1 rounded bg-black/70 px-1 font-mono text-[11px] text-white">
              {formatTimecode(f.timestamp_sec)}
            </span>
          </button>
        ))}
      </div>

      <div className="mt-4 rounded-lg bg-surface-muted p-3 text-sm">
        <p className="text-xs text-muted">Правило и источник</p>
        {v.rules.length === 0 ? (
          <p className="mt-0.5 text-muted">
            Ссылка на норматив ожидает проверки.
          </p>
        ) : (
          <ul className="mt-0.5 space-y-1">
            {v.rules.map((r) => (
              <li key={r.rule_id}>
                <a
                  href={r.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  {r.document}
                </a>
                <span className="text-muted">, {r.provision}</span>
                {!r.verified && (
                  <span className="ml-1 rounded bg-danger-soft px-1 text-xs text-danger">
                    не проверено
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

function Notice({
  title,
  tone,
  children,
}: {
  title: string;
  tone?: "danger";
  children: React.ReactNode;
}) {
  return (
    <div
      className={`rounded-xl border p-5 ${
        tone === "danger"
          ? "border-danger/30 bg-danger-soft"
          : "border-border bg-surface"
      }`}
    >
      <p className={`font-semibold ${tone === "danger" ? "text-danger" : ""}`}>
        {title}
      </p>
      <p className="mt-1 text-sm text-muted">{children}</p>
    </div>
  );
}
