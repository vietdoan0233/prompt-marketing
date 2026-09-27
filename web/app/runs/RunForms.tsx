"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiSend } from "@/lib/client";
import type { IngestionRun, Source } from "@/lib/types";

import styles from "./runs.module.css";

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
      <div className={styles.forms}>
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
          <div>
            <h2>Estonia import</h2>
            <p className="panel-desc">
              Official basic data, annual reports, EMTAK activity and indicator files are imported with source
              provenance.
            </p>
          </div>
          <div className={`form-row ${styles.eeRow}`}>
            <label>
              Minimum employees
              <input name="min_employees" type="number" min={0} defaultValue={20} />
            </label>
            <p className={styles.eeHint}>
              Scope uses the latest 2024/2025 reported FTE; 0 imports every eligible entity.
            </p>
            <button className="btn btn-primary" disabled={busy || !eeSource}>
              {busy ? "Importing…" : "Run Estonia import"}
            </button>
          </div>
          <fieldset className={styles.yearGroup}>
            <legend>Report years</legend>
            <div className={styles.years}>
              {YEARS.map((year) => (
                <label key={year} className="check-chip">
                  <input type="checkbox" name="years" value={year} defaultChecked /> {year}
                </label>
              ))}
            </div>
          </fieldset>
          {!eeSource && (
            <div className={`notice ${styles.gateNotice}`}>
              No Estonian register source is configured, so this import cannot run.
            </div>
          )}
          {eeSource && !eeSource.ingestible && (
            <div className={`notice ${styles.gateNotice}`}>
              <strong>Permission gate:</strong> {eeSource.gate_reasons.join("; ")}
            </div>
          )}
          <p className={styles.formNote}>
            Financial values are reported values; unavailable EBITDA, currency and unit remain —. Dividends and capex
            are stored when reported but are not shown, since no current row has either populated.
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
          <div>
            <h2>Import manual Estonia CSV</h2>
            <p className="panel-desc">
              Upload a UTF-8 or Windows-1252 CSV (comma or semicolon) from a source approved for manual ingestion.
            </p>
          </div>
          <div className={`form-row ${styles.csvRow}`}>
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
            <label className={styles.narrow}>
              Min employees
              <input name="min_employees" type="number" min={0} defaultValue={20} />
            </label>
            <label className={styles.file}>
              File
              <input name="file" type="file" accept=".csv,text/csv" required />
            </label>
          </div>
          <div className="row">
            <button className="btn btn-primary" disabled={busy || csvSources.length === 0}>
              {busy ? "Importing…" : "Start manual import"}
            </button>
          </div>
          <p className={styles.formNote}>Disabled or unapproved sources fail closed before storage.</p>
        </form>
      </div>
      {error && (
        <div className="notice notice-bad" role="alert">
          {error}
        </div>
      )}
    </>
  );
}
