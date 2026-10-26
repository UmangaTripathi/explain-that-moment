import type { Stats } from "../api";

const ROWS: [string, string][] = [
  ["xg", "xG"],
  ["shots", "Shots"],
  ["shots_on_target", "On target"],
  ["possession_pct", "Possession %"],
  ["high_regains", "High regains"],
  ["final_third_entries", "Final-third entries"],
];

export function StatsPanel({ stats, home, away }: { stats: Stats | null; home: string; away: string }) {
  return (
    <div className="panel">
      <h3>Live stats</h3>
      {!stats ? <p className="muted">Starts with the replay</p> : (
        <div className="stats">
          {ROWS.map(([key, labelText]) => {
            const h = stats.teams[home]?.[key] ?? 0;
            const a = stats.teams[away]?.[key] ?? 0;
            const share = h + a === 0 ? 50 : (h / (h + a)) * 100;
            return (
              <div key={key} className="stat">
                <div className="stat-row"><span>{h}</span><span className="muted">{labelText}</span><span>{a}</span></div>
                <div className="bar"><div className="home" style={{ width: `${share}%` }} /></div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
