"use client";

import { useEffect, useRef, useState } from "react";
import { RecordIcon } from "@/components/review/ReviewShell";
import styles from "@/components/review/review.module.css";

function durationLabel(seconds: number) {
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${remainder}` : `${minutes}:${remainder}`;
}

export function HistoryVideoPreview({ id, filename }: { id: string; filename: string }) {
  const containerRef = useRef<HTMLSpanElement>(null);
  const [requested, setRequested] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const extension = filename.split(".").at(-1)?.toUpperCase();
  const format = extension && ["MP4", "MOV", "M4V"].includes(extension) ? extension : "ВИДЕО";

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setRequested(true);
        observer.disconnect();
      }
    }, { rootMargin: "160px" });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  return (
    <span ref={containerRef} className={styles.videoPreview} data-ready={ready && !failed} aria-hidden="true">
      <span className={styles.previewPlaceholder}><RecordIcon /><span>{failed ? "Превью недоступно" : "Видеозапись"}</span></span>
      {requested && !failed && <video
        src={`/api/jobs/${encodeURIComponent(id)}/video`}
        muted
        playsInline
        preload="metadata"
        tabIndex={-1}
        disablePictureInPicture
        onLoadedMetadata={(event) => {
          const video = event.currentTarget;
          if (Number.isFinite(video.duration) && video.duration > 0) {
            setDuration(video.duration);
            // Seek a fraction into the source to decode a still without playing.
            try { video.currentTime = Math.min(.1, video.duration / 2); } catch { /* The first decoded frame is also a valid preview. */ }
          }
        }}
        onLoadedData={() => setReady(true)}
        onSeeked={() => setReady(true)}
        onError={() => setFailed(true)}
      />}
      <span className={styles.previewPlay}><svg viewBox="0 0 24 24" fill="currentColor"><path d="m9 5 11 7-11 7V5Z" /></svg></span>
      <span className={styles.previewDuration}>{duration != null ? durationLabel(duration) : format}</span>
    </span>
  );
}
