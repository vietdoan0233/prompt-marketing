"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition, useEffect, useRef } from "react";

// Route error boundary: keeps the top bar/footer, explains the failure and offers a real retry. Server errors
// reach here with their message redacted in production, so the copy never depends on backend detail.
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const router = useRouter();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  const unreachable = error.message?.includes("not reachable");

  return (
    <section className="panel state-panel" role="alert">
      <p className="state-code">{unreachable ? "API unavailable" : "Request failed"}</p>
      <h1 ref={heading} tabIndex={-1} style={{ fontSize: 34 }}>
        {unreachable ? "The company database is not reachable" : "This page could not be loaded"}
      </h1>
      <p className="subtitle">
        {unreachable
          ? "The API did not respond. Check that the API service is running, then retry."
          : "The API returned an error for this request. Nothing was changed. Retry, or go back to the company list."}
      </p>
      <div className="row" style={{ marginTop: 20 }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() =>
            startTransition(() => {
              router.refresh();
              reset();
            })
          }
        >
          Retry
        </button>
        <Link href="/companies" className="btn">
          Company list
        </Link>
      </div>
      {error.digest && <p className="form-hint" style={{ marginTop: 14 }}>Reference {error.digest}</p>}
    </section>
  );
}
