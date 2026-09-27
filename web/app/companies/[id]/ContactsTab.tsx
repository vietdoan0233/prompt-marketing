import { Badge } from "@/components/Badge";
import { EmptyState, Panel } from "@/components/ui";
import { fmtDate } from "@/lib/api";
import type { CompanyDetail, Source } from "@/lib/types";

import { EraseContactButton } from "./ClientControls";
import styles from "./company.module.css";

const ERASURE_STEPS = [
  {
    n: "01",
    title: "Request reference",
    body: "The reviewer is asked for the data-subject request reference (e.g. DSR-2026-014). It is stored with the suppression record and the audit event.",
  },
  {
    n: "02",
    title: "Erase contact",
    body: "The contact row is hard-deleted from the database. There is no soft-delete copy to restore.",
  },
  {
    n: "03",
    title: "Redact retained input",
    body: "Raw CSV inputs retained for retry from the same source have the contact's name, email, phone and profile URL replaced with [erased]. Snapshots never hold contact fields.",
  },
  {
    n: "04",
    title: "Audit and block",
    body: "One-way hashes of the identity and email block any later re-import of the same person. The audit event records the basis, reference, reason and counts, never the personal data.",
  },
];

export function ContactsTab({ d, sources }: { d: CompanyDetail; sources: Source[] | null }) {
  const sourceById = new Map((sources ?? []).map((s) => [s.id, s]));

  return (
    <>
      <div className={styles.contactsRow}>
        <Panel
          title="Contacts (personal data)"
          description="Shown only as supplied by an approved source. Emails and phone numbers are never guessed or enriched."
          className="pii"
        >
          {d.contacts.length === 0 ? (
            <EmptyState>No source-backed contacts.</EmptyState>
          ) : (
            <div className={styles.contactGrid}>
              {d.contacts.map((p) => {
                const src = sourceById.get(p.source_id);
                return (
                  <article key={p.id} className={`subpanel ${styles.contactCard}`}>
                    <div className={styles.contactHead}>
                      <div>
                        <div className={styles.contactName}>{p.name}</div>
                        <div className="small muted">{p.role ?? "role not supplied"}</div>
                      </div>
                      {src?.permission_status === "approved" && (
                        <Badge value="approved" label="Approved source" title={`${src.name} · permission approved`} />
                      )}
                    </div>
                    <dl className={`kv ${styles.kvCompact}`}>
                      <dt>Email</dt>
                      <dd>{p.email ?? <span className="muted">not supplied · never guessed</span>}</dd>
                      <dt>Phone</dt>
                      <dd>{p.phone ?? <span className="muted">not supplied · never guessed</span>}</dd>
                      {p.profile_url && (
                        <>
                          <dt>Profile</dt>
                          <dd>
                            <a href={p.profile_url} target="_blank" rel="noreferrer" className={styles.breakAll}>
                              {p.profile_url}
                            </a>
                          </dd>
                        </>
                      )}
                      {p.country && (
                        <>
                          <dt>Country</dt>
                          <dd>{p.country}</dd>
                        </>
                      )}
                      <dt>Source</dt>
                      <dd>
                        <span className="mono">{p.source_id}</span>
                        {p.source_url && (
                          <>
                            {" · "}
                            <a href={p.source_url} target="_blank" rel="noreferrer">
                              source ↗
                            </a>
                          </>
                        )}
                      </dd>
                      <dt>Basis</dt>
                      <dd>{p.contact_basis}</dd>
                      <dt>Usage policy</dt>
                      <dd>{p.usage_policy}</dd>
                      <dt>Confidence</dt>
                      <dd>
                        <Badge value={p.confidence} />
                      </dd>
                      <dt>Last verified</dt>
                      <dd>{fmtDate(p.last_verified_at)}</dd>
                    </dl>
                    <div className={styles.eraseRow}>
                      <EraseContactButton contactId={p.id} />
                      <span className="form-hint">Asks for a data-subject request reference before erasing.</span>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </Panel>

        <Panel title="Handling rules" description="How contact data is treated in this database.">
          <ul className="dot-list">
            <li>Show a contact only when an approved source supplied it.</li>
            <li>Never infer or enrich an email address or phone number, including from naming conventions.</li>
            <li>Never place contact details in audit details or in LLM prompts.</li>
            <li>A verified erasure blocks the same person from being re-imported later.</li>
          </ul>
        </Panel>
      </div>

      <Panel
        title="Erasure action and audit trail"
        description="What the Erase (GDPR) button does, in order."
      >
        <ol className={styles.steps}>
          {ERASURE_STEPS.map((s) => (
            <li key={s.n} className={styles.step}>
              <span className={styles.stepNum}>{s.n}</span>
              <strong className={styles.stepTitle}>{s.title}</strong>
              <p className="small muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </Panel>
    </>
  );
}
