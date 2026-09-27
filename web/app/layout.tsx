import type { Metadata } from "next";
import Link from "next/link";

import { ActorPicker } from "@/components/ActorPicker";
import { Nav } from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "Mergero Company Database",
  description: "Internal, permission-gated, provenance-linked Estonia company database.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/companies" className="brand" aria-label="Mergero company database home">
            <span className="brand-word">MERGERO</span>
            <span className="brand-sub">Company database</span>
          </Link>
          <Nav />
          <ActorPicker />
        </header>
        <main className="main">{children}</main>
        <footer className="footer">
          <div className="footer-inner">
            <strong>Internal use only</strong>
            <span>
              EE register data · Scope ends at ingestion, review and data-quality reporting · No outreach actions
            </span>
          </div>
        </footer>
      </body>
    </html>
  );
}
