"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { formatBytes } from "@/lib/format";

const MAX_BYTES = 500 * 1024 * 1024;
const ACCEPT = ".mov,.mp4,.m4v,video/quicktime,video/mp4";

type State =
  | { kind: "idle" }
  | { kind: "uploading"; file: File; loaded: number }
  | { kind: "error"; message: string };

/** XHR rather than fetch, because fetch can't report upload progress. */
function upload(file: File, onProgress: (loaded: number) => void) {
  return new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      // FastAPI puts error messages in `detail`.
      let body: { job_id?: string; detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      if (xhr.status < 300 && body.job_id) resolve(body.job_id);
      else
        reject(
          new Error(
            typeof body.detail === "string"
              ? body.detail
              : `Ошибка сервера (${xhr.status})`,
          ),
        );
    };
    xhr.onerror = () => reject(new Error("Нет соединения с сервером"));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export function UploadPanel() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [dragging, setDragging] = useState(false);

  async function start(file: File) {
    if (!/\.(mov|mp4|m4v)$/i.test(file.name)) {
      setState({ kind: "error", message: "Поддерживаются файлы .mov и .mp4" });
      return;
    }
    if (file.size > MAX_BYTES) {
      setState({ kind: "error", message: "Файл больше 500 МБ" });
      return;
    }
    setState({ kind: "uploading", file, loaded: 0 });
    try {
      const id = await upload(file, (loaded) =>
        setState({ kind: "uploading", file, loaded }),
      );
      router.push(`/jobs/?id=${encodeURIComponent(id)}`);
    } catch (err) {
      setState({ kind: "error", message: (err as Error).message });
    }
  }

  const busy = state.kind === "uploading";
  const pct = busy
    ? Math.min(100, Math.round((state.loaded / state.file.size) * 100))
    : 0;

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!busy) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files[0];
        if (file && !busy) start(file);
      }}
      className={`rounded-xl border-2 border-dashed bg-surface p-8 text-center transition-colors sm:p-12 ${
        dragging ? "border-accent bg-accent-soft" : "border-border"
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) start(file);
        }}
      />

      {busy ? (
        <div className="mx-auto max-w-md">
          <p className="truncate font-medium">{state.file.name}</p>
          <p className="mt-1 text-sm text-muted">
            Загрузка… {formatBytes(state.loaded)} из{" "}
            {formatBytes(state.file.size)}
          </p>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-muted">
            <div
              className="h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${pct}%` }}
            />
          </div>
          {pct === 100 && (
            <p className="mt-3 text-sm text-muted">Сохраняем видео…</p>
          )}
        </div>
      ) : (
        <>
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="mx-auto size-10 text-muted"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          </svg>
          <p className="mt-4 text-lg font-medium">
            Перетащите видеозапись сюда
          </p>
          <p className="mt-1 text-sm text-muted">
            .mov или .mp4, до 500 МБ
          </p>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="mt-6 rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90"
          >
            Выбрать файл
          </button>
          {state.kind === "error" && (
            <p className="mt-4 text-sm text-danger" role="alert">
              {state.message}
            </p>
          )}
        </>
      )}
    </div>
  );
}
