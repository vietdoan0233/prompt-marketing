import type { Metadata } from "next";
import Link from "next/link";

import { ActorPicker } from "@/components/ActorPicker";
import { Nav } from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "Mergero Seller Signals",
  description: "Explainable seller-prospect signals backed by Estonia register and annual-report evidence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/seller-prospects" className="brand">
            Mergero <span>seller signals</span>
          </Link>
          <Nav />
          <ActorPicker />
        </header>
        <main className="main">{children}</main>
        <footer className="footer">
          Internal use only · Signals support advisor review; they do not establish owner intent or buyer fit
        </footer>
      </body>
    </html>
  );
}
