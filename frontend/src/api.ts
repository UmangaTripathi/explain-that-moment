export const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Player = { id: string; name: string; number: number; position: string };
export type Team = { id: string; name: string; short: string; formation: string; players: Player[] };

export type MatchSummary = { id: string; home: string; away: string; final_score: string };
export type MatchInfo = {
  id: string; venue: string; date: string; competition: string; home: Team; away: Team;
  duration: number; languages: Record<string, string>; personas: string[];
};

export type EventView = {
  id: string; time: string; team: string | null; player: string | null; type: string;
  outcome?: string; xg?: number; x?: number; y?: number; end_x?: number; end_y?: number;
  related_player?: string;
};

export type Moment = {
  id: string; minute: number; type: string; team: string | null; event_ids: string[];
  importance: number; summary: string;
};

export type Claim = {
  text: string; event_ids: string[]; status: "pending" | "verified" | "rejected";
  reason: string | null; injected: boolean;
};

export type TraceStep = { agent: string; step: string; tool?: string; ms?: number; rejected?: number };

export type Card = {
  moment: Moment; headline: string; narrative: string; claims: Claim[]; language: string;
  persona: string; verifier_rounds: number; red_team: boolean; trace: TraceStep[];
};

export type Recap = {
  headline: string; story: string;
  turning_point?: { minute: number; why: string };
  player_of_the_match?: { name: string; why: string };
  by_the_numbers?: string[];
};

export type TeamStats = Record<string, number>;
export type Stats = { window: string; teams: Record<string, TeamStats> };

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: body ? JSON.stringify(body) : undefined,
});

export const api = {
  matches: () => json<MatchSummary[]>("/matches"),
  match: (id: string) => json<MatchInfo>(`/matches/${id}`),
  replay: (id: string, speed: number, redTeam: boolean) =>
    json(`/matches/${id}/replay?speed=${speed}&red_team=${redTeam}`, post()),
  evidence: (id: string, ids: string[]) =>
    json<EventView[]>(`/matches/${id}/evidence?ids=${ids.join(",")}`),
  stats: (id: string, until: number) => json<Stats>(`/matches/${id}/stats?until=${until}`),
  narrate: (id: string, momentId: string, persona: string, language: string) =>
    json<{ headline: string; narrative: string }>(
      `/matches/${id}/moments/${momentId}/narrate`, post({ persona, language })),
  recap: (id: string, persona: string, language: string) =>
    json<Recap>(`/matches/${id}/recap`, post({ persona, language })),
  stream: (id: string) => new EventSource(`${API_URL}/matches/${id}/stream`),
};
