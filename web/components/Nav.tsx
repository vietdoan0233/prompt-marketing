"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/seller-prospects", label: "Seller prospects" },
  { href: "/companies", label: "Companies" },
  { href: "/runs", label: "Ingestion runs" },
  { href: "/sources", label: "Source registry" },
  { href: "/quality", label: "Data quality" },
  { href: "/audit", label: "Audit log" },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav className="nav">
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} className={path.startsWith(l.href) ? "active" : ""}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
