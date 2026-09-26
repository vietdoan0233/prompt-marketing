"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiSend } from "@/lib/client";
import type { IngestionRun, Source } from "@/lib/types";

const YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025];

export function RunForms({ sources }: { sources: Source[] }) {
  const router = useRouter();
  const eeSource = sources.find((s) => s.connector_type === "ee_ariregister");
  const csvSources = sources.filter((s) => s.connector_type === "csv");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(fn: () => Promise<IngestionRun>) {
    setBusy(true);
    setError(null);
    try {
      const run = await fn();
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  const str = (fd: FormData, key: string) => String(fd.get(key) ?? "").trim();

  return (
    <>
      <form
        className="panel form"
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const years = fd.getAll("years").map(Number);
          submit(() =>
            apiSend<IngestionRun>("POST", "/ingestion-runs", {
              source_id: eeSource?.id,
              query: { years },
              min_employees: Number(str(fd, "min_employees") || 20),
            }),
          );
        }}
      >
        <h2>Estonia import</h2>
        <div className="form-row">
          <label>
            Minimum employees
            <input name="min_employees" type="number" min={0} defaultValue={20} style={{ width: 100 }} />
          </label>
          <span className="small muted">Scope uses the latest 2024/2025 reported FTE; 0 imports every eligible entity.</span>
          <button className="btn btn-primary" disabled={busy || !eeSource}>
            {busy ? "Importing…" : "Run Estonia import"}
          </button>
        </div>
        <div className="chips">
          {YEARS.map((year) => (
            <label key={year} className="chip" style={{ flexDirection: "row", gap: 4 }}>
              <input type="checkbox" name="years" value={year} defaultChecked /> {year}
            </label>
          ))}
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Official basic data, annual reports, EMTAK activity and indicator files are imported with provenance.
          Financial values are reported values; unavailable EBITDA, dividends, capex, currency and unit remain —.
          {eeSource && !eeSource.ingestible && ` Permission gate: ${eeSource.gate_reasons.join("; ")}`}
        </p>
      </form>

      <form
        className="panel form"
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          submit(() => apiSend<IngestionRun>("POST", "/ingestion-runs", fd));
        }}
      >
        <h2>Import manual Estonia CSV</h2>
        <div className="form-row">
          <label>
            Source
            <select name="source_id" required defaultValue="mergero-manual">
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
          <button className="btn btn-primary" disabled={busy || csvSources.length === 0}>
            {busy ? "Importing…" : "Start import"}
          </button>
        </div>
      </form>
      {error && <div className="notice notice-bad">{error}</div>}
    </>
  );
}
