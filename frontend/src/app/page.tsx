import { UploadPanel } from "@/components/UploadPanel";

// Same build-time limit as the upload panel: 95 on Cloudflare Workers, 500 self-hosted.
const MAX_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB ?? 500);

export default function Home() {
  return (
    <div className="home-workspace">
      <header className="workspace-intro">
        <h1>Проверка кассовых операций</h1>
        <p>Загрузите видео. Проверьте отмеченные события и фрагменты записи.</p>
      </header>
      <div id="upload" className="upload-scene">
        <div className="upload-composition"><UploadPanel /></div>
      </div>
      <section className="workspace-help" aria-label="Справка">
        <details id="how-it-works">
          <summary>Как проходит проверка <span aria-hidden="true">+</span></summary>
          <ol>
            <li><strong>Добавьте записи.</strong> Выберите видео MOV, MP4 или M4V до {MAX_MB} МБ. Перед отправкой можно убрать лишние файлы.</li>
            <li><strong>Отправьте на анализ.</strong> Страница проверки откроется автоматически после загрузки. Если записей несколько, переключайтесь между ними на странице проверки.</li>
            <li><strong>Проверьте события.</strong> Сопоставьте отмеченные фрагменты, таймкоды и нормативы. Решение остаётся за проверяющим.</li>
          </ol>
        </details>
        <details id="api">
          <summary>API для интеграции <span aria-hidden="true">+</span></summary>
          <p>Передайте ключ в заголовке <code>X-API-Key</code>. В ответ на загрузку придёт <code>job_id</code> для получения статуса и результата.</p>
          <pre>{`curl -X POST $HOST/api/v1/analyze \\
  -H "X-API-Key: <ключ>" -F "file=@video.mov"

curl $HOST/api/v1/jobs/<job_id> \\
  -H "X-API-Key: <ключ>"`}</pre>
        </details>
      </section>
    </div>
  );
}
