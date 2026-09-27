// Shown while a route's server data loads on navigation. Reserves the header and panel space so the page does
// not jump when data arrives. Static by design: no shimmer.
export default function Loading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="visually-hidden">Loading…</span>
      <div className="skeleton-line" style={{ width: 220 }} />
      <div className="skeleton-title" />
      <div className="skeleton-line" style={{ width: "min(640px, 90%)", marginBottom: 28 }} />
      <div className="skeleton" style={{ height: 120, marginBottom: 20 }} />
      <div className="skeleton" style={{ height: 420 }} />
    </div>
  );
}
