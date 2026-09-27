// Shared layout primitives for the dark advisor-workspace look. Server-safe (no hooks), so they can be used
// from React Server Components and client components alike. Styling lives in app/globals.css.

import Link from "next/link";
import { Fragment, type ReactNode } from "react";

export type Crumb = { label: string; href?: string };
export type Tone = "good" | "warn" | "bad" | "accent" | "info" | "muted";

/** Eyebrow breadcrumb, serif title, subtitle, optional meta row (identity + badges) and right-hand actions. */
export function PageHeader({
  crumbs,
  title,
  subtitle,
  meta,
  actions,
}: {
  crumbs: Crumb[];
  title: ReactNode;
  subtitle?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-main">
        <nav className="eyebrow" aria-label="Breadcrumb">
          {crumbs.map((c, i) => (
            <Fragment key={`${c.label}-${i}`}>
              {i > 0 && (
                <span className="sep" aria-hidden="true">
                  /
                </span>
              )}
              {c.href ? <Link href={c.href}>{c.label}</Link> : <span>{c.label}</span>}
            </Fragment>
          ))}
        </nav>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
        {meta && <div className="page-meta">{meta}</div>}
      </div>
      {actions && <div className="page-head-aside">{actions}</div>}
    </header>
  );
}

/** Rounded dark card with a title, muted description, optional actions and footnote. */
export function Panel({
  title,
  description,
  actions,
  footnote,
  id,
  className,
  children,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  footnote?: ReactNode;
  id?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section className={`panel${className ? ` ${className}` : ""}`} id={id}>
      {(title || description || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p className="panel-desc">{description}</p>}
          </div>
          {actions && <div className="action">{actions}</div>}
        </div>
      )}
      {children}
      {footnote && <p className="panel-foot">{footnote}</p>}
    </section>
  );
}

/** Stat card: large serif figure, label, optional source/explanation line. */
export function Stat({
  value,
  label,
  sub,
  tone,
}: {
  value: ReactNode;
  label: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={`stat${tone ? ` tone-${tone}` : ""}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/** Horizontal bar. `value` is a 0–1 share; the bar keeps a visible sliver for non-zero values. */
export function Meter({
  value,
  tone,
  label,
}: {
  value: number;
  tone?: "good" | "warn" | "bad" | "final";
  label?: string;
}) {
  const pct = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
  return (
    <div className={`meter${tone ? ` ${tone}` : ""}`} role="img" aria-label={label}>
      <span style={{ width: `${pct === 0 ? 0 : Math.max(1, pct * 100)}%` }} />
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
