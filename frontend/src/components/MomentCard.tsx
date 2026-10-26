import { useState } from "react";
import type { Card } from "../api";
import { MOMENT_LABEL } from "./Timeline";

type Props = {
  card: Card; headline: string; narrative: string; rtl: boolean;
  onEvidence: (title: string, ids: string[]) => void;
};

export function MomentCard({ card, headline, narrative, rtl, onEvidence }: Props) {
  const [open, setOpen] = useState(false);
  const verified = card.claims.filter((c) => c.status === "verified");
  const rejected = card.claims.filter((c) => c.status === "rejected");
  const tools = card.trace.filter((t) => t.step === "tool");

  return (
    <article className={`card moment ${card.moment.type}`}>
      <header>
        <span className="badge">{card.moment.minute}'</span>
        <span className={`type ${card.moment.type}`}>{MOMENT_LABEL[card.moment.type] ?? card.moment.type}</span>
        <span className="spacer" />
        <span className="verify-chip ok">✓ {verified.length} verified</span>
        {rejected.length > 0 && <span className="verify-chip warn">⛔ {rejected.length} blocked</span>}
      </header>
      <h2 dir={rtl ? "rtl" : "ltr"}>{headline}</h2>
      <p className="narrative" dir={rtl ? "rtl" : "ltr"}>{narrative}</p>

      <button className="link" onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} the evidence behind this ({card.claims.length} claims · {tools.length} data lookups)
      </button>

      {open && (
        <div className="claims">
          {card.claims.map((c, i) => (
            <div key={i} className={`claim ${c.status} ${c.injected ? "injected" : ""}`}>
              <div className="claim-head">
                <span className="status">
                  {c.status === "verified" ? "✓ Verified" : c.injected ? "⛔ Planted & blocked" : "⛔ Blocked"}
                </span>
                <button className="link small" onClick={() => onEvidence(c.text, c.event_ids)}>
                  {c.event_ids.length} cited event{c.event_ids.length === 1 ? "" : "s"} →
                </button>
              </div>
              <p>{c.text}</p>
              {c.reason && <p className="reason">Verifier: {c.reason}</p>}
            </div>
          ))}
          <div className="pipeline">
            {card.trace.map((t, i) => (
              <span key={i} className={`step ${t.agent.split(":")[0]}`}>
                {t.agent.split(":")[0]}{t.tool ? ` · ${t.tool}` : t.step === "hard_checks" ? " · hard checks" : ""}
              </span>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}
