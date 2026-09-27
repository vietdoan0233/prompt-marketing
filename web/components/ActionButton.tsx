"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

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
  /** Render the button disabled; `disabledReason` explains why (shown as helper text and tooltip). */
  disabled?: boolean;
  disabledReason?: string;
  size?: "sm";
  /** Stretch the button to its container (stacked decision panels). */
  block?: boolean;
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
  disabled = false,
  disabledReason,
  size,
  block,
}: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Guards against a second activation before React re-renders the disabled state.
  const inFlight = useRef(false);

  async function run() {
    if (disabled || inFlight.current) return;
    if (confirm && !window.confirm(confirm)) return;
    let payload = body;
    if (promptReason) {
      const reason = window.prompt(promptReason);
      if (reason === null) return; // cancelled: no request
      if (reason.trim().length < 3) {
        setError("Not sent: a reason of at least 3 characters is required.");
        return;
      }
      payload = { ...(body as object), reason: reason.trim() };
    }
    inFlight.current = true;
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
      inFlight.current = false;
      setBusy(false);
    }
  }

  const classes = ["btn", `btn-${variant}`, size === "sm" ? "btn-sm" : "", block ? "btn-block" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={`action${block ? " action-block" : ""}`}>
      <button
        type="button"
        className={classes}
        onClick={run}
        disabled={disabled || busy}
        aria-busy={busy || undefined}
        title={disabled ? disabledReason : undefined}
        data-pending={busy || undefined}
      >
        {label}
      </button>
      <span className="visually-hidden" role="status">
        {busy ? `${label}: in progress` : ""}
      </span>
      {disabled && disabledReason && <span className="form-hint">{disabledReason}</span>}
      {error && (
        <span className="error-inline" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
