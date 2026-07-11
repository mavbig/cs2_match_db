"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/matches", label: "Matches" },
  { href: "/search", label: "Search" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <header
      style={{
        borderBottom: "1px solid var(--border)",
        background: "var(--surface)",
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}
    >
      <div
        className="container"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          height: "3.5rem",
        }}
      >
        <Link href="/" style={{ fontWeight: 700, fontSize: "1.1rem", color: "var(--text)" }}>
          CS2 Match DB
        </Link>
        <nav style={{ display: "flex", gap: "0.25rem" }}>
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              style={{
                padding: "0.4rem 0.85rem",
                borderRadius: "6px",
                color: pathname === l.href ? "var(--accent)" : "var(--muted)",
                background: pathname === l.href ? "var(--surface2)" : "transparent",
                fontWeight: pathname === l.href ? 600 : 400,
                fontSize: "0.9rem",
              }}
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
