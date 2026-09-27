"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { apiSend } from "@/lib/client";
import type { DigitalDecayRunResult, Fact } from "@/lib/types";

import styles from "./company.module.css";

const CORRECTABLE = [
  "employees",
  "legal_name",
  "trading_name",
  "city",
  "website",
  "industry_code",
  "sector",
  "ownership_type",
  "description",
];

/** Button text that keeps the width of its widest state, so a pending label never shifts the layout. */
function StableLabel({ label, pendingLabel, pending }: { label: string; pendingLabel: string; pending: boolean }) {
  return (
    <span className={styles.stableLabel}>
      <span className={pending ? styles.labelHidden : undefined}>{label}</span>
      <span className={pending ? undefined : styles.labelHidden}>{pendingLabel}</span>
    </span>
  );
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function CorrectionForm({ companyId, facts }: { companyId: string; facts: Fact[] }) {
  const router = useRouter();
  const [field, setField] = useState("employees");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Guards against a second submit before React re-renders the disabled state.
  const inFlight = useRef(false);
  const fieldFacts = facts.filter((f) => f.field_name === field && !f.is_correction);

  return (
    <form
      className="form"
      aria-busy={busy || undefined}
      onSubmit={async (e) => {
        e.preventDefault();
        if (inFlight.current) return;
        const form = e.currentTarget;
        const fd = new FormData(form);
        inFlight.current = true;
        setBusy(true);
        setError(null);
        setOk(null);
        try {
          await apiSend("POST", `/companies/${companyId}/facts/corrections`, {
            field_name: field,
            value: String(fd.get("value") ?? ""),
            reason: String(fd.get("reason") ?? ""),
            corrects_fact_id: fd.get("corrects_fact_id") || null,
            evidence_url: fd.get("evidence_url") || null,
          });
          setOk("Correction saved. Original source facts are preserved below.");
          // Only a saved correction clears the form; after a failure the reviewer's input stays as typed.
          form.reset();
          router.refresh();
        } catch (err) {
          setError(`Correction not saved. ${errorText(err)}`);
        } finally {
          inFlight.current = false;
          setBusy(false);
        }
      }}
    >
      <div className={`form-row ${styles.correctionRow}`}>
        <label>
          Field
          <select value={field} onChange={(e) => setField(e.target.value)}>
            {CORRECTABLE.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label>
          Corrected value
          <input
            name="value"
            required
            placeholder={field === "employees" ? "e.g. 24 or 20-49" : field === "ownership_type" ? "founder-led" : ""}
          />
        </label>
        <label>
          Corrects fact
          <select name="corrects_fact_id" defaultValue="">
            <option value="">all current source facts</option>
            {fieldFacts.map((f) => (
              <option key={f.id} value={f.id}>
                {f.source_id}: {JSON.stringify(f.value_json)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Reason · required
        <input name="reason" required minLength={3} placeholder="e.g. confirmed in 2025 annual report" />
        <span className="form-hint">Stored in the audit trail with your reviewer identity.</span>
      </label>
      <label>
        Evidence URL · optional
        <input name="evidence_url" type="url" placeholder="https://…" />
      </label>
      <div className={styles.formActions}>
        <button
          className="btn btn-primary"
          disabled={busy}
          aria-busy={busy || undefined}
          data-pending={busy || undefined}
        >
          <StableLabel label="Save correction" pendingLabel="Saving…" pending={busy} />
        </button>
        <span className="form-hint">Original source facts and their provenance are kept.</span>
      </div>
      {error && (
        <div className="notice notice-bad small" role="alert">
          {error}
        </div>
      )}
      <div role="status">{ok && <div className="notice notice-good">{ok}</div>}</div>
    </form>
  );
}

const REVIEW_LABEL: Record<string, string> = { reviewed: "reviewed", needs_correction: "needs correction" };

export function ReviewControls({ companyId, status }: { companyId: string; status: string }) {
  const router = useRouter();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const inFlight = useRef(false);

  async function set(review_status: string) {
    if (inFlight.current) return;
    const label = REVIEW_LABEL[review_status] ?? review_status;
    const note = window.prompt(`Set review status to "${label}". Optional note:`);
    if (note === null) return; // Cancel: nothing is sent.
    inFlight.current = true;
    setPending(review_status);
    setError(null);
    setOk(null);
    try {
      // An empty note after OK is a deliberate "no note".
      await apiSend("POST", `/companies/${companyId}/review`, { review_status, note: note.trim() || undefined });
      setOk(`Review status set to ${label}.`);
      router.refresh();
    } catch (e) {
      setError(`Review status not changed. ${errorText(e)}`);
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  }

  const busy = pending !== null;
  return (
    <div className={styles.controlGroup}>
      <span className="action">
        {status !== "reviewed" && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => set("reviewed")}
            disabled={busy}
            aria-busy={pending === "reviewed" || undefined}
            data-pending={pending === "reviewed" || undefined}
          >
            <StableLabel label="Mark reviewed" pendingLabel="Saving…" pending={pending === "reviewed"} />
          </button>
        )}
        {status !== "needs_correction" && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => set("needs_correction")}
            disabled={busy}
            aria-busy={pending === "needs_correction" || undefined}
            data-pending={pending === "needs_correction" || undefined}
          >
            <StableLabel label="Needs correction" pendingLabel="Saving…" pending={pending === "needs_correction"} />
          </button>
        )}
      </span>
      <span role="status" className={ok && !busy ? `small tone-text-good ${styles.controlNote}` : "visually-hidden"}>
        {busy ? "Saving review status…" : (ok ?? "")}
      </span>
      {error && (
        <div className={`notice notice-bad small ${styles.controlNotice}`} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

export function EraseContactButton({ contactId }: { contactId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  return (
    <span className="action">
      <button
        type="button"
        className="btn btn-danger"
        disabled={busy}
        aria-busy={busy || undefined}
        data-pending={busy || undefined}
        onClick={async () => {
          if (inFlight.current) return;
          const ref = window.prompt(
            "GDPR erasure: this permanently deletes the contact, redacts retained raw inputs and blocks re-import.\n\nData-subject request reference (e.g. DSR-2026-014):",
          );
          if (ref === null) return; // Cancel: nothing is sent.
          const params = new URLSearchParams({ reason: "GDPR erasure request" });
          if (ref.trim()) params.set("request_reference", ref.trim());
          inFlight.current = true;
          setBusy(true);
          setError(null);
          try {
            await apiSend("DELETE", `/contacts/${contactId}?${params.toString()}`);
            router.refresh();
          } catch (e) {
            setError(`Not erased. ${errorText(e)}`);
          } finally {
            inFlight.current = false;
            setBusy(false);
          }
        }}
      >
        <StableLabel label="Erase (GDPR)" pendingLabel="Erasing…" pending={busy} />
      </button>
      <span className="visually-hidden" role="status">
        {busy ? "Erasing contact…" : ""}
      </span>
      {error && (
        <span className="error-inline" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}

export function RunDecayCheck({ companyId, hasResult }: { companyId: string; hasResult: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A run without new evidence (e.g. a failed fetch) is reported as such, never as a finished check.
  const [result, setResult] = useState<{ ok: boolean; text: string; runId: string } | null>(null);
  const inFlight = useRef(false);
  return (
    <div className={styles.controlGroup}>
      <button
        type="button"
        className="btn btn-primary"
        disabled={busy}
        aria-busy={busy || undefined}
        data-pending={busy || undefined}
        onClick={async () => {
          if (inFlight.current) return;
          inFlight.current = true;
          setBusy(true);
          setError(null);
          setResult(null);
          try {
            const r = await apiSend<DigitalDecayRunResult>("POST", `/companies/${companyId}/signals/digital-decay`);
            setResult(
              r.signal
                ? {
                    // insufficient_evidence is a finished check, but not a determinate result: neutral tone.
                    ok: r.signal.verdict !== "insufficient_evidence",
                    text: `Check finished: ${r.signal.verdict.replaceAll("_", " ")}.`,
                    runId: r.run_id,
                  }
                : {
                    ok: false,
                    text: `Run ${r.status.replaceAll("_", " ")}: no new website evidence was recorded.`,
                    runId: r.run_id,
                  },
            );
            router.refresh();
          } catch (e) {
            setError(errorText(e));
          } finally {
            inFlight.current = false;
            setBusy(false);
          }
        }}
      >
        <StableLabel label={hasResult ? "Re-run check" : "Run check"} pendingLabel="Checking website…" pending={busy} />
      </button>
      <span className="visually-hidden" role="status">
        {busy ? "Website check running…" : ""}
      </span>
      {error && (
        <div className={`notice notice-bad small ${styles.controlNotice}`} role="alert">
          <strong>Website check not run.</strong> {error}
        </div>
      )}
      {result && !busy && (
        <div className={`notice ${result.ok ? "notice-good" : ""} small ${styles.controlNotice}`} role="status">
          {result.text} <Link href={`/runs/${result.runId}`}>Open run</Link>
        </div>
      )}
    </div>
  );
}
