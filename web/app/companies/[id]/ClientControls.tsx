"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiSend } from "@/lib/client";
import type { Fact } from "@/lib/types";

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

export function CorrectionForm({ companyId, facts }: { companyId: string; facts: Fact[] }) {
  const router = useRouter();
  const [field, setField] = useState("employees");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fieldFacts = facts.filter((f) => f.field_name === field && !f.is_correction);

  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const fd = new FormData(form);
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
          form.reset();
          router.refresh();
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="form-row">
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
        Reason (required, stored in audit trail)
        <input name="reason" required minLength={3} placeholder="e.g. confirmed in 2025 annual report" />
      </label>
      <label>
        Evidence URL (optional)
        <input name="evidence_url" type="url" placeholder="https://…" />
      </label>
      <div>
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Saving…" : "Save correction"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {ok && <div className="notice notice-good">{ok}</div>}
    </form>
  );
}

export function ReviewControls({ companyId, status }: { companyId: string; status: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  async function set(review_status: string) {
    const note = window.prompt(`Set review status to "${review_status}". Optional note:`) ?? undefined;
    try {
      await apiSend("POST", `/companies/${companyId}/review`, { review_status, note });
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <span className="action">
      {status !== "reviewed" && (
        <button className="btn btn-primary" onClick={() => set("reviewed")}>
          Mark reviewed
        </button>
      )}
      {status !== "needs_correction" && (
        <button className="btn btn-ghost" onClick={() => set("needs_correction")}>
          Needs correction
        </button>
      )}
      {error && <span className="error-inline">{error}</span>}
    </span>
  );
}

export function EraseContactButton({ contactId }: { contactId: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="action">
      <button
        className="btn btn-danger"
        onClick={async () => {
          const ref = window.prompt(
            "GDPR erasure: this permanently deletes the contact, redacts retained raw inputs and blocks re-import.\n\nData-subject request reference (e.g. DSR-2026-014):",
          );
          if (ref === null) return;
          const params = new URLSearchParams({ reason: "GDPR erasure request" });
          if (ref.trim()) params.set("request_reference", ref.trim());
          try {
            await apiSend("DELETE", `/contacts/${contactId}?${params.toString()}`);
            router.refresh();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          }
        }}
      >
        Erase (GDPR)
      </button>
      {error && <span className="error-inline">{error}</span>}
    </span>
  );
}
