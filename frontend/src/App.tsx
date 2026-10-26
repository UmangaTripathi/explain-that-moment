import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, type Card, type EventView, type MatchInfo, type MatchSummary, type Moment, type Recap,
  type Stats,
} from "./api";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { MomentCard } from "./components/MomentCard";
import { RecapPanel } from "./components/RecapPanel";
import { StatsPanel } from "./components/StatsPanel";
import { Timeline } from "./components/Timeline";

type Status = "idle" | "live" | "finishing" | "finished" | "error";
type Activity = { id: number; text: string; tone: "info" | "ok" | "warn" };

export default function App() {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [matchId, setMatchId] = useState("match_001");
  const [info, setInfo] = useState<MatchInfo | null>(null);
  const [speed, setSpeed] = useState(30);
  const [redTeam, setRedTeam] = useState(true);
  const [persona, setPersona] = useState("casual");
  const [language, setLanguage] = useState("en");

  const [status, setStatus] = useState<Status>("idle");
  const [minute, setMinute] = useState(0);
  const [score, setScore] = useState<string>("");
  const [moments, setMoments] = useState<Moment[]>([]);
  const [cards, setCards] = useState<Record<string, Card>>({});
  const [analysing, setAnalysing] = useState<Set<string>>(new Set());
  const [feed, setFeed] = useState<EventView[]>([]);
  const [recap, setRecap] = useState<Recap | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [evidence, setEvidence] = useState<{ title: string; ids: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-narrations per (moment, persona, language); English/casual comes from the card itself.
  const [retold, setRetold] = useState<Record<string, { headline: string; narrative: string }>>({});
  const [localRecap, setLocalRecap] = useState<Record<string, Recap>>({});
  const activityId = useRef(0);

  const log = useCallback((text: string, tone: Activity["tone"] = "info") => {
    setActivity((a) => [{ id: ++activityId.current, text, tone }, ...a].slice(0, 60));
  }, []);

  useEffect(() => {
    api.matches().then(setMatches).catch((e) => setError(`Backend unreachable: ${e.message}`));
  }, []);

  useEffect(() => {
    setInfo(null);
    api.match(matchId).then(setInfo).catch((e) => setError(e.message));
  }, [matchId]);

  // Live stream subscription.
  useEffect(() => {
    const source = api.stream(matchId);
    const on = (kind: string, fn: (data: any) => void) =>
      source.addEventListener(kind, (e) => fn(JSON.parse((e as MessageEvent).data)));

    on("status", (d) => {
      setStatus(d.state);
      if (d.state === "live") log(`Replay started at ${d.speed}x`, "info");
      if (d.state === "finishing") log("Full time: waiting for agents, then writing the recap", "info");
      if (d.state === "error") setError(d.detail);
    });
    on("clock", (d) => { setMinute(d.minute); setScore(d.score); });
    on("event", (d: EventView) => setFeed((f) => [d, ...f].slice(0, 40)));
    on("moment", (m: Moment) => {
      setMoments((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, m]));
      log(`Detector: ${m.summary}`, "info");
    });
    on("agent", (d) => {
      if (d.state === "analysing") {
        setAnalysing((s) => new Set(s).add(d.moment_id));
        log(`Analyst investigating ${d.moment_id}`, "info");
      } else if (d.state === "failed") {
        setAnalysing((s) => { const n = new Set(s); n.delete(d.moment_id); return n; });
        log(`Agents failed on ${d.moment_id}: ${d.detail}`, "warn");
      }
    });
    on("card", (c: Card) => {
      setCards((cs) => ({ ...cs, [c.moment.id]: c }));
      setAnalysing((s) => { const n = new Set(s); n.delete(c.moment.id); return n; });
      const caught = c.claims.filter((x) => x.status === "rejected");
      const tools = c.trace.filter((t) => t.step === "tool").length;
      log(`Card ready ${c.moment.minute}': ${tools} tool calls, ${c.verifier_rounds} verify round(s)`, "ok");
      for (const x of caught) {
        log(`Verifier blocked${x.injected ? " planted" : ""} claim: ${x.reason}`, "warn");
      }
    });
    on("recap", (r: Recap) => { setRecap(r); log("Recap agent: full-time story ready", "ok"); });
    source.onerror = () => { /* EventSource reconnects automatically */ };
    return () => source.close();
  }, [matchId, log]);

  // Live stats refresh every few game minutes.
  useEffect(() => {
    if (status === "idle") return;
    api.stats(matchId, minute).then(setStats).catch(() => undefined);
  }, [matchId, Math.floor(minute / 3), status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Persona / language changes re-tell verified cards (no new facts).
  useEffect(() => {
    if (persona === "casual" && language === "en") return;
    for (const card of Object.values(cards)) {
      const key = `${card.moment.id}|${persona}|${language}`;
      if (retold[key]) continue;
      setRetold((r) => ({ ...r, [key]: { headline: "…", narrative: "Translating…" } }));
      api.narrate(matchId, card.moment.id, persona, language)
        .then((t) => setRetold((r) => ({ ...r, [key]: t })))
        .catch(() => setRetold((r) => { const n = { ...r }; delete n[key]; return n; }));
    }
  }, [cards, persona, language]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (status !== "finished" || (persona === "casual" && language === "en")) return;
    const key = `${persona}|${language}`;
    if (localRecap[key]) return;
    api.recap(matchId, persona, language).then((r) => setLocalRecap((x) => ({ ...x, [key]: r })));
  }, [status, persona, language]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    setMoments([]); setCards({}); setFeed([]); setRecap(null); setActivity([]);
    setRetold({}); setLocalRecap({}); setAnalysing(new Set()); setMinute(0); setStats(null);
    try {
      await api.replay(matchId, speed, redTeam);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const shownCards = useMemo(
    () => Object.values(cards).sort((a, b) => b.moment.minute - a.moment.minute || b.moment.id.localeCompare(a.moment.id)),
    [cards],
  );
  const verifiedCount = shownCards.reduce((n, c) => n + c.claims.filter((x) => x.status === "verified").length, 0);
  const blockedCount = shownCards.reduce((n, c) => n + c.claims.filter((x) => x.status === "rejected").length, 0);

  const [homeGoals, awayGoals] = (score.match(/(\d+)-(\d+)/)?.slice(1) ?? ["0", "0"]).map(Number);
  const recapToShow = persona === "casual" && language === "en" ? recap : localRecap[`${persona}|${language}`] ?? null;
  const rtl = language === "ar";

  return (
    <div className="app" dir="ltr">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◉</span>
          <div>
            <h1>Explain That Moment</h1>
            <p>Verified, explainable match intelligence</p>
          </div>
        </div>
        <div className="controls">
          <label>Match
            <select value={matchId} onChange={(e) => setMatchId(e.target.value)} disabled={status === "live"}>
              {matches.map((m) => (
                <option key={m.id} value={m.id}>{m.home} v {m.away}</option>
              ))}
            </select>
          </label>
          <label>Speed
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {[15, 30, 60, 120].map((s) => <option key={s} value={s}>{s}x</option>)}
            </select>
          </label>
          <label>Viewer
            <select value={persona} onChange={(e) => setPersona(e.target.value)}>
              {(info?.personas ?? ["casual", "tactical"]).map((p) => (
                <option key={p} value={p}>{p === "casual" ? "Casual fan" : "Tactics fan"}</option>
              ))}
            </select>
          </label>
          <label>Language
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {Object.entries(info?.languages ?? { en: "English" }).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          <label className="toggle" title="Plant one false claim to show the Verifier catching it">
            <input type="checkbox" checked={redTeam} onChange={(e) => setRedTeam(e.target.checked)} />
            Red-team test
          </label>
          <button className="primary" onClick={start}>
            {status === "live" ? "Restart" : "▶ Kick off"}
          </button>
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      <section className="scoreboard">
        <div className="team home">{info?.home.name ?? "Home"}</div>
        <div className="score">
          <span>{homeGoals}</span><span className="dash">–</span><span>{awayGoals}</span>
        </div>
        <div className="team away">{info?.away.name ?? "Away"}</div>
        <div className="clock">
          <span className={`pill ${status}`}>{status === "idle" ? "Ready" : status === "live" ? "LIVE" : status === "finishing" ? "FT · analysing" : status === "finished" ? "Full time" : "Error"}</span>
          <span className="minute">{minute}'</span>
        </div>
        <div className="trust">
          <strong>{verifiedCount}</strong> claims verified · <strong className="warn">{blockedCount}</strong> blocked
        </div>
      </section>

      <Timeline duration={info?.duration ?? 95} minute={minute} moments={moments} cards={cards}
        homeName={info?.home.name ?? ""} />

      <main className="layout">
        <section className="feed">
          {recapToShow && <RecapPanel recap={recapToShow} rtl={rtl} />}
          {status === "finished" && !recapToShow && <div className="card skeleton">Writing the recap in your language…</div>}
          {[...analysing].map((id) => {
            const m = moments.find((x) => x.id === id);
            return m ? (
              <div key={id} className="card skeleton">
                <span className="badge">{m.minute}'</span> Analyst investigating: {m.summary}
                <div className="dots"><span /><span /><span /></div>
              </div>
            ) : null;
          })}
          {shownCards.length === 0 && analysing.size === 0 && !recapToShow && (
            <div className="empty">
              <h2>Press Kick off</h2>
              <p>The match replays as a live feed. When something important happens, the Analyst
                agent investigates it with data tools, the Verifier checks every claim against the
                match events, and the Narrator tells it your way. Click any claim to see its evidence.</p>
            </div>
          )}
          {shownCards.map((card) => {
            const t = retold[`${card.moment.id}|${persona}|${language}`];
            return (
              <MomentCard key={card.moment.id} card={card}
                headline={t?.headline ?? card.headline} narrative={t?.narrative ?? card.narrative} rtl={rtl}
                onEvidence={(title, ids) => setEvidence({ title, ids })} />
            );
          })}
        </section>

        <aside className="side">
          <StatsPanel stats={stats} home={info?.home.name ?? ""} away={info?.away.name ?? ""} />
          <div className="panel">
            <h3>Agent activity</h3>
            <ul className="activity">
              {activity.length === 0 && <li className="muted">Waiting for kick-off…</li>}
              {activity.map((a) => <li key={a.id} className={a.tone}>{a.text}</li>)}
            </ul>
          </div>
          <div className="panel">
            <h3>Live events</h3>
            <ul className="events">
              {feed.length === 0 && <li className="muted">No events yet</li>}
              {feed.map((e) => (
                <li key={e.id}><span className="t">{e.time}</span> {label(e)}</li>
              ))}
            </ul>
          </div>
        </aside>
      </main>

      {evidence && info && (
        <EvidenceDrawer matchId={matchId} title={evidence.title} ids={evidence.ids}
          homeName={info.home.name} onClose={() => setEvidence(null)} />
      )}
    </div>
  );
}

function label(e: EventView): string {
  const who = e.player ? `${e.player} (${e.team})` : e.team ?? "";
  switch (e.type) {
    case "goal": return `⚽ GOAL ${who}`;
    case "shot": return `Shot ${who}, ${e.outcome?.replace("_", " ")} (xG ${e.xg})`;
    case "card": return `${e.outcome === "yellow" ? "🟨" : "🟥"} ${who}`;
    case "substitution": return `⇄ ${e.related_player} on for ${e.player}`;
    case "kickoff": return `Kick-off ${e.team ?? ""}`;
    case "half_time": return "Half time";
    case "full_time": return "Full time";
    default: return `${e.type} ${who}`;
  }
}
