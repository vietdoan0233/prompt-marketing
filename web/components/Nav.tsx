"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/companies", label: "Companies" },
  { href: "/seller-prospects", label: "Seller prospects" },
  { href: "/runs", label: "Ingestion" },
  { href: "/sources", label: "Sources" },
  { href: "/quality", label: "Quality" },
  { href: "/audit", label: "Audit" },
];

// Rendered inside <Link>: shows that a navigation to this section is in flight (some pages take seconds).
function NavLabel({ label }: { label: string }) {
  const { pending } = useLinkStatus();
  return (
    <>
      {label}
      {pending && <span className="nav-pending" aria-hidden="true" />}
      {pending && <span className="visually-hidden">(loading)</span>}
    </>
  );
}

export function Nav() {
  const path = usePathname();
  return (
    <nav className="nav" aria-label="Main">
      {LINKS.map((l) => {
        const active = path.startsWith(l.href);
        return (
          <Link key={l.href} href={l.href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>
            <NavLabel label={l.label} />
          </Link>
        );
      })}
    </nav>
  );
}
