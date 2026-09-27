"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/companies", label: "Companies" },
  { href: "/seller-prospects", label: "Seller prospects" },
  { href: "/runs", label: "Ingestion" },
  { href: "/sources", label: "Sources" },
  { href: "/quality", label: "Quality" },
  { href: "/audit", label: "Audit" },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav className="nav" aria-label="Main">
      {LINKS.map((l) => {
        const active = path.startsWith(l.href);
        return (
          <Link key={l.href} href={l.href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>
            {l.label}
          </Link>
        );
      })}
    </nav>
  );
}
