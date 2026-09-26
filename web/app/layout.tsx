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
          <Link href="/companies" className="brand">
            Mergero <span>company database</span>
          </Link>
          <Nav />
          <ActorPicker />
        </header>
        <main className="main">{children}</main>
        <footer className="footer">
          Internal use only · Scope ends at ingestion, review and data-quality reporting · No outreach actions
        </footer>
      </body>
    </html>
  );
}
