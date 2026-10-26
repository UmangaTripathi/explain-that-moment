import type { Recap } from "../api";

export function RecapPanel({ recap, rtl }: { recap: Recap; rtl: boolean }) {
  return (
    <article className="card recap" dir={rtl ? "rtl" : "ltr"}>
      <span className="eyebrow">Full-time recap</span>
      <h2>{recap.headline}</h2>
      <p>{recap.story}</p>
      <div className="recap-grid">
        {recap.turning_point && (
          <div><span className="eyebrow">Turning point · {recap.turning_point.minute}'</span><p>{recap.turning_point.why}</p></div>
        )}
        {recap.player_of_the_match && (
          <div><span className="eyebrow">Player of the match</span>
            <p><strong>{recap.player_of_the_match.name}</strong>: {recap.player_of_the_match.why}</p></div>
        )}
      </div>
      {recap.by_the_numbers && recap.by_the_numbers.length > 0 && (
        <ul className="numbers">{recap.by_the_numbers.map((n, i) => <li key={i}>{n}</li>)}</ul>
      )}
    </article>
  );
}
