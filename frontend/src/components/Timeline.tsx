import type { Card, Moment } from "../api";

export const MOMENT_LABEL: Record<string, string> = {
  goal: "Goal",
  big_chance: "Big chance",
  momentum_swing: "Momentum swing",
  pressing_win: "Pressing win",
  red_card: "Red card",
  key_substitution: "Super sub",
};

type Props = {
  duration: number; minute: number; moments: Moment[]; cards: Record<string, Card>; homeName: string;
};

export function Timeline({ duration, minute, moments, cards }: Props) {
  const pct = (m: number) => `${Math.min(100, (m / duration) * 100)}%`;
  return (
    <div className="timeline" aria-label="Match timeline">
      <div className="track">
        <div className="progress" style={{ width: pct(minute) }} />
        <div className="half" style={{ left: pct(45) }}><span>HT</span></div>
        {moments.map((m) => (
          <div key={m.id} className={`marker ${m.type} ${cards[m.id] ? "done" : ""}`}
            style={{ left: pct(m.minute) }}
            title={`${m.minute}' ${MOMENT_LABEL[m.type] ?? m.type}: ${m.summary}`} />
        ))}
      </div>
      <div className="ticks">
        {[0, 15, 30, 45, 60, 75, 90].map((t) => <span key={t} style={{ left: pct(t) }}>{t}'</span>)}
      </div>
    </div>
  );
}
