"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiSend } from "@/lib/client";

type Props = {
  method?: "POST" | "DELETE";
  path: string;
  body?: unknown;
  label: string;
  confirm?: string;
  /** Ask for a free-text reason and send it as body.reason (appended to body). */
  promptReason?: string;
  variant?: "primary" | "danger" | "ghost";
  /** Navigate to the created run (response.id) instead of refreshing. Must stay serializable (RSC prop). */
  openRun?: boolean;
};

export function ActionButton({
  method = "POST",
  path,
  body,
  label,
  confirm,
  promptReason,
  variant = "ghost",
  openRun,
}: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (confirm && !window.confirm(confirm)) return;
    let payload = body;
    if (promptReason) {
      const reason = window.prompt(promptReason);
      if (!reason || reason.trim().length < 3) return;
      payload = { ...(body as object), reason: reason.trim() };
    }
    setBusy(true);
    setError(null);
    try {
      const data = await apiSend(method, path, payload);
      const runId = openRun ? (data as { id?: string } | null)?.id : undefined;
      if (runId) router.push(`/runs/${runId}`);
      else router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="action">
      <button className={`btn btn-${variant}`} onClick={run} disabled={busy}>
        {busy ? "…" : label}
      </button>
      {error && (
        <span className="error-inline" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
