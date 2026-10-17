import { UploadPanel } from "@/components/UploadPanel";

const steps = [
  {
    title: "Загрузите запись",
    text: "Видео с камеры над кассой в формате .mov.",
  },
  {
    title: "Дождитесь анализа",
    text: "Модели работают локально; обычно это занимает пару минут.",
  },
  {
    title: "Проверьте фрагменты",
    text: "Тип события, таймкод, кадры и ссылка на норматив.",
  },
];

export default function Home() {
  return (
    <div className="space-y-12">
      <section className="space-y-6">
        <div className="max-w-2xl">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            Проверка видеозаписи кассовых операций
          </h1>
          <p className="mt-2 text-muted">
            Сервис ищет визуально наблюдаемые признаки нарушений кассовой
            дисциплины и показывает, где именно на записи они видны.
          </p>
        </div>
        <UploadPanel />
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        {steps.map((s, i) => (
          <div
            key={s.title}
            className="rounded-xl border border-border bg-surface p-5"
          >
            <span className="font-mono text-xs text-muted">0{i + 1}</span>
            <h2 className="mt-1 font-medium">{s.title}</h2>
            <p className="mt-1 text-sm text-muted">{s.text}</p>
          </div>
        ))}
      </section>

      <section
        id="api"
        className="scroll-mt-20 rounded-xl border border-border bg-surface p-5"
      >
        <h2 className="font-medium">API</h2>
        <p className="mt-1 text-sm text-muted">
          Ключ передаётся в заголовке <code className="font-mono">X-API-Key</code>
          . Загрузка возвращает <code className="font-mono">job_id</code>;
          статус: <code className="font-mono">processing</code>,{" "}
          <code className="font-mono">done</code> или{" "}
          <code className="font-mono">failed</code>.
        </p>
        <pre className="mt-4 overflow-x-auto rounded-lg bg-surface-muted p-4 font-mono text-xs leading-relaxed">
          {`curl -X POST $HOST/api/v1/analyze \\
     -H "X-API-Key: <ключ>" -F "file=@video_017.mov"

curl $HOST/api/v1/jobs/<job_id> -H "X-API-Key: <ключ>"`}
        </pre>
      </section>
    </div>
  );
}
