import { useEffect, useState } from "react";
import { api, type EventView } from "../api";

type Props = { matchId: string; title: string; ids: string[]; homeName: string; onClose: () => void };

export function EvidenceDrawer({ matchId, title, ids, homeName, onClose }: Props) {
  const [events, setEvents] = useState<EventView[] | null>(null);

  useEffect(() => {
    setEvents(null);
    api.evidence(matchId, ids).then(setEvents).catch(() => setEvents([]));
  }, [matchId, ids]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Evidence">
        <header>
          <h3>Evidence</h3>
          <button className="link" onClick={onClose}>Close ✕</button>
        </header>
        <blockquote>{title}</blockquote>
        {events === null ? <p className="muted">Loading…</p> : (
          <>
            {events.length === 0 && <p className="warn">None of the cited events exist in the match data.</p>}
            <Pitch events={events} homeName={homeName} />
            <table>
              <thead><tr><th>Time</th><th>Event</th><th>Player</th><th>Detail</th></tr></thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td>{e.time}</td>
                    <td><span className={`dot ${e.team === homeName ? "home" : "away"}`} />{e.type}</td>
                    <td>{e.player ?? "—"}{e.related_player ? ` → ${e.related_player}` : ""}</td>
                    <td>{[e.outcome, e.xg != null ? `xG ${e.xg}` : null].filter(Boolean).join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">Event IDs: {ids.join(", ")}</p>
          </>
        )}
      </aside>
    </div>
  );
}

/** Mini pitch: home attacks left→right, away right→left (event coords are per acting team). */
function Pitch({ events, homeName }: { events: EventView[]; homeName: string }) {
  const W = 105, H = 68;
  const pos = (e: EventView, x?: number, y?: number) => {
    if (x == null || y == null) return null;
    const home = e.team === homeName;
    const px = home ? x : 100 - x;
    const py = home ? y : 100 - y;
    return { x: (px / 100) * W, y: (py / 100) * H };
  };
  return (
    <svg className="pitch" viewBox={`-2 -2 ${W + 4} ${H + 4}`} aria-label="Pitch map of cited events">
      <rect x="0" y="0" width={W} height={H} className="grass" />
      <line x1={W / 2} y1="0" x2={W / 2} y2={H} />
      <circle cx={W / 2} cy={H / 2} r="9.15" />
      <rect x="0" y={H / 2 - 20.16} width="16.5" height="40.32" />
      <rect x={W - 16.5} y={H / 2 - 20.16} width="16.5" height="40.32" />
      <rect x="0" y={H / 2 - 9.16} width="5.5" height="18.32" />
      <rect x={W - 5.5} y={H / 2 - 9.16} width="5.5" height="18.32" />
      {events.map((e, i) => {
        const a = pos(e, e.x, e.y);
        if (!a) return null;
        const b = pos(e, e.end_x, e.end_y);
        const cls = e.team === homeName ? "home" : "away";
        return (
          <g key={e.id} className={cls}>
            {b && <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="pass" />}
            <circle cx={a.x} cy={a.y} r={e.type === "shot" || e.type === "goal" ? 2.2 : 1.5}
              className={e.type} />
            <text x={a.x + 2} y={a.y - 2}>{i + 1}</text>
          </g>
        );
      })}
    </svg>
  );
}
