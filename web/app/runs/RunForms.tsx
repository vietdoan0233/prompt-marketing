"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiSend } from "@/lib/client";
import type { IngestionRun, Source } from "@/lib/types";

const REGISTRY_CONNECTORS = ["brreg", "prh", "zefix", "cvr"];
const COUNTRIES = ["DE", "AT", "CH", "FI", "SE", "NO", "DK", "IS"];

export function RunForms({ sources }: { sources: Source[] }) {
  const router = useRouter();
  const csvSources = sources.filter((s) => s.connector_type === "csv");
  const registrySources = sources.filter((s) => REGISTRY_CONNECTORS.includes(s.connector_type));
  const webSources = sources.filter((s) => s.connector_type === "website");
  const [registry, setRegistry] = useState(registrySources[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const registryType = registrySources.find((s) => s.id === registry)?.connector_type;

  async function submit(fn: () => Promise<IngestionRun>) {
    setBusy(true);
    setError(null);
    try {
      const run = await fn();
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      router.refresh(); // rejected runs are recorded too
    } finally {
      setBusy(false);
    }
  }

  const str = (fd: FormData, k: string) => String(fd.get(k) ?? "").trim();

  return (
    <>
      <div className="grid grid-2">
        <form
          className="panel form"
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            const query: Record<string, unknown> = { max_records: Number(str(fd, "max_records") || 100) };
            const code = str(fd, "industry_code");
            if (code) query[registryType === "prh" ? "mainBusinessLine" : "industry_code"] = code;
            if (registryType === "prh") {
              if (str(fd, "location")) query.location = str(fd, "location");
              query.companyForm = "OY";
            }
            if (registryType === "zefix") query.name = str(fd, "name");
            submit(() =>
              apiSend<IngestionRun>("POST", "/ingestion-runs", {
                source_id: registry,
                query,
                min_employees: Number(str(fd, "min_employees") || 20),
              }),
            );
          }}
        >
          <h2>Registry discovery (A)</h2>
          <div className="form-row">
            <label>
              Registry
              <select value={registry} onChange={(e) => setRegistry(e.target.value)}>
                {registrySources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.id} {s.ingestible ? "" : "(gate will reject)"}
                  </option>
                ))}
              </select>
            </label>
            {registryType !== "zefix" && (
              <label>
                {registryType === "prh" ? "TOL 2025 code" : "NACE prefix"}
                <input name="industry_code" placeholder={registryType === "prh" ? "62100" : "62"} style={{ width: 90 }} />
              </label>
            )}
            {registryType === "prh" && (
              <label>
                Town
                <input name="location" placeholder="Tampere" style={{ width: 110 }} />
              </label>
            )}
            {registryType === "zefix" && (
              <label>
                Company name contains
                <input name="name" required minLength={3} placeholder="e.g. Informatik" />
              </label>
            )}
            <label>
              Min employees
              <input name="min_employees" type="number" min={0} defaultValue={20} style={{ width: 80 }} />
            </label>
            <label>
              Max records
              <input name="max_records" type="number" min={1} max={1000} defaultValue={100} style={{ width: 80 }} />
            </label>
          </div>
          <p className="small muted" style={{ margin: 0 }}>
            Live official APIs. Brreg and CVR filter ≥ min employees at source; PRH and Zefix publish no headcount, so
            those companies stay “headcount unknown”. Zefix and CVR fail closed until credentials are configured.
          </p>
          <div>
            <button className="btn btn-primary" disabled={busy || !registry}>
              {busy ? "Running…" : "Run discovery"}
            </button>
          </div>
        </form>

        <form
          className="panel form"
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            const countries = fd.getAll("countries").map(String);
            submit(() =>
              apiSend<IngestionRun>("POST", "/ingestion-runs", {
                source_id: str(fd, "source_id"),
                query: {
                  countries: countries.length ? countries : undefined,
                  qualified_only: fd.get("qualified_only") === "on",
                  max_records: Number(str(fd, "max_records") || 25),
                },
                min_employees: 20,
              }),
            );
          }}
        >
          <h2>Website enrichment (C)</h2>
          <div className="form-row">
            <label>
              Source
              <select name="source_id">
                {webSources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.id} ({s.countries.join(", ")})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Max domains
              <input name="max_records" type="number" min={1} max={200} defaultValue={25} style={{ width: 80 }} />
            </label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input name="qualified_only" type="checkbox" defaultChecked /> only ≥ 20 employees
            </label>
          </div>
          <div className="chips">
            {COUNTRIES.map((c) => (
              <label key={c} className="chip" style={{ flexDirection: "row", gap: 4 }}>
                <input type="checkbox" name="countries" value={c} /> {c}
              </label>
            ))}
          </div>
          <p className="small muted" style={{ margin: 0 }}>
            Crawls websites that registries published for companies already in the database: Impressum (legal name,
            register number, VAT, managing directors), about/team (founder & family signals), careers (open positions).
            robots.txt respected, 1 request/second per site.
          </p>
          <div>
            <button className="btn btn-primary" disabled={busy}>
              {busy ? "Crawling…" : "Run enrichment"}
            </button>
          </div>
        </form>
      </div>

      <form
        className="panel form"
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          submit(() => apiSend<IngestionRun>("POST", "/ingestion-runs", fd));
        }}
      >
        <h2>Import CSV (approved export or Mergero list)</h2>
        <div className="form-row">
          <label>
            Source
            <select name="source_id" required defaultValue="mergero-csv">
              {csvSources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id} {s.ingestible ? "" : "(gate will reject)"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Min employees
            <input name="min_employees" type="number" min={0} defaultValue={20} style={{ width: 90 }} />
          </label>
          <label>
            File (UTF-8 or Windows-1252, comma/semicolon)
            <input name="file" type="file" accept=".csv,text/csv" required />
          </label>
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Importing…" : "Start import"}
          </button>
        </div>
      </form>
      {error && <div className="notice notice-bad">{error}</div>}
    </>
  );
}
