import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { JetBrains_Mono } from "next/font/google";
import localFont from "next/font/local";
import { UploadTransitionProvider } from "@/components/UploadPageTransition";
import { AppNav } from "@/components/AppNav";
import "./globals.css";

const openSans = localFont({
  src: "./fonts/open-sans/OpenSans-Variable.ttf",
  variable: "--font-open-sans",
  weight: "300 800",
  style: "normal",
  display: "swap",
});

const mono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin", "cyrillic"],
});

export const metadata: Metadata = {
  title: "Forte — Контроль кассы",
  description:
    "Проверка видеозаписей кассовых операций на признаки нарушений кассовой дисциплины",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ru"
      className={`${openSans.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col font-sans">
        <a href="#main-content" className="skip-link">Перейти к содержимому</a>
        <header className="app-header">
          <div className="app-header-inner">
            <div className="app-brand">
              <Link href="/" aria-label="Forte — контроль кассы">
                <Image src="/forte-wordmark.svg" alt="Forte" width={156} height={46} className="app-logo" priority />
              </Link>
              <span>Внутренний контроль</span>
            </div>
            <AppNav />
          </div>
        </header>
        <main id="main-content" className="main-content flex-1" tabIndex={-1}>
          <UploadTransitionProvider>{children}</UploadTransitionProvider>
        </main>
        <footer className="app-footer">
          <span>Forte · Видеоаналитика</span>
          <p>Результат помогает проверить запись. Окончательное решение принимает специалист.</p>
        </footer>
      </body>
    </html>
  );
}
