import Link from "next/link";

export default function NotFound() {
  return (
    <section className="panel state-panel">
      <p className="state-code">404</p>
      <h1 style={{ fontSize: 34 }}>Nothing exists at this address</h1>
      <p className="subtitle">
        If you followed a link to a company, ingestion run or duplicate candidate, it may have been merged into
        another record or never existed in this database.
      </p>
      <div className="row" style={{ marginTop: 20 }}>
        <Link href="/companies" className="btn btn-primary">
          Company list
        </Link>
        <Link href="/runs" className="btn">
          Ingestion runs
        </Link>
      </div>
    </section>
  );
}
