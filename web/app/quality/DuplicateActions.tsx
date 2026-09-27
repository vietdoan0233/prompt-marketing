// Reviewer decision buttons for one duplicate candidate. Used by the quality list and the candidate review page.
// Merging is hidden when the candidate carries a registry_id "against" reason: different national registry IDs
// are different legal entities (the API would refuse the merge), so only link or dismiss are offered.
// `unavailableReason` (e.g. the comparison evidence could not be loaded) hides merge and disables the other
// decisions, stating why.

import { ActionButton } from "@/components/ActionButton";
import type { Duplicate } from "@/lib/types";

import { registryIdsDisagree } from "./format";
import styles from "./quality.module.css";

/** What a merge does NOT move (api/app/services/quality.py moves identifiers, facts and contacts only). */
export const MERGE_SCOPE_NOTE =
  "A merge moves facts, identifiers and contacts. Financial rows, registered addresses and shareholders stay on the merged-away record; see its page.";

export function DuplicateActions({
  d,
  stacked = false,
  unavailableReason,
}: {
  d: Duplicate;
  stacked?: boolean;
  unavailableReason?: string;
}) {
  const nameA = d.company_a_name ?? "company A";
  const nameB = d.company_b_name ?? "company B";
  const disabled = Boolean(unavailableReason);
  const canMerge = !registryIdsDisagree(d) && !disabled;
  // The reason is shown once below the buttons rather than repeated under each one.
  const common = { disabled, size: stacked ? undefined : ("sm" as const), block: stacked };
  return (
    <div className={stacked ? styles.decisionStack : styles.decisionInline}>
      {canMerge && (
        <>
          <ActionButton
            {...common}
            path={`/quality/duplicates/${d.id}/merge`}
            body={{ survivor_id: d.company_a_id }}
            label={`Merge into “${nameA}”`}
            promptReason="Why are these the same legal entity?"
            variant="primary"
          />
          <ActionButton
            {...common}
            path={`/quality/duplicates/${d.id}/merge`}
            body={{ survivor_id: d.company_b_id }}
            label={`Merge into “${nameB}”`}
            promptReason="Why are these the same legal entity?"
          />
          <p className={styles.mergeNote}>
            {stacked
              ? MERGE_SCOPE_NOTE
              : "Merge leaves financial rows, registered addresses and shareholders on the merged-away record."}
          </p>
        </>
      )}
      <ActionButton
        {...common}
        path={`/quality/duplicates/${d.id}/link`}
        label="Link as related"
        promptReason="How are they related (e.g. group subsidiary)?"
      />
      <ActionButton
        {...common}
        path={`/quality/duplicates/${d.id}/dismiss`}
        label="Not a duplicate"
        promptReason="Why are these different companies?"
        variant="danger"
      />
      {unavailableReason && <p className={styles.unavailable}>{unavailableReason}</p>}
    </div>
  );
}
