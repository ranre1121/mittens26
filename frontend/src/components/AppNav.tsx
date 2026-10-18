"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/components/AuthGate";

export function AppNav() {
  const pathname = usePathname();
  const { session, signOut } = useAuth();
  return (
    <nav className="app-nav" aria-label="Основная навигация">
      <Link href="/#upload">Новая проверка</Link>
      <Link href="/history" aria-current={pathname.replace(/\/$/, "") === "/history" ? "page" : undefined}>История</Link>
      <Link href="/#how-it-works">Инструкция</Link>
      <Link href="/#api">API</Link>
      {session.status === "signed-in" && session.method === "cookie" && (
        <button type="button" className="app-nav-logout" onClick={signOut}>Выйти</button>
      )}
    </nav>
  );
}
