"use client";

import Image from "next/image";
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
    <header className="site-header">
      <div className="container site-header-inner">
        <Link href="/" className="brand-link">
          <Image
            src="/logo_with_name.png"
            alt="CS2 Match DB"
            width={180}
            height={40}
            priority
            style={{ height: "36px", width: "auto" }}
          />
        </Link>
        <nav className="site-nav">
          {links.map((l) => {
            const active = pathname === l.href || (l.href !== "/" && pathname.startsWith(l.href));
            return (
              <Link key={l.href} href={l.href} className={`nav-link${active ? " is-active" : ""}`}>
                {l.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <style jsx>{`
        .site-header {
          border-bottom: 1px solid var(--border);
          background: rgba(17, 24, 32, 0.85);
          backdrop-filter: blur(12px);
          position: sticky;
          top: 0;
          z-index: 50;
        }
        .site-header-inner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          height: 4rem;
          gap: 1rem;
        }
        .brand-link {
          display: flex;
          align-items: center;
          flex-shrink: 0;
        }
        .brand-link:hover {
          opacity: 0.92;
        }
        .site-nav {
          display: flex;
          gap: 0.25rem;
          flex-wrap: wrap;
          justify-content: flex-end;
        }
        .nav-link {
          padding: 0.45rem 0.9rem;
          border-radius: var(--radius);
          color: var(--muted);
          font-size: 0.9rem;
          font-weight: 500;
          transition: background 0.15s, color 0.15s;
        }
        .nav-link:hover {
          color: var(--text);
          background: var(--surface2);
        }
        .nav-link.is-active {
          color: var(--accent);
          background: rgba(59, 158, 255, 0.1);
          font-weight: 600;
        }
        @media (max-width: 640px) {
          .site-header-inner {
            flex-direction: column;
            height: auto;
            padding-top: 0.75rem;
            padding-bottom: 0.75rem;
          }
        }
      `}</style>
    </header>
  );
}
