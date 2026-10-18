"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav className="app-nav" aria-label="Основная навигация">
      <Link href="/#upload">Новая проверка</Link>
      <Link href="/history" aria-current={pathname === "/history" ? "page" : undefined}>История</Link>
      <Link href="/#how-it-works">Инструкция</Link>
      <Link href="/#api">API</Link>
    </nav>
  );
}
