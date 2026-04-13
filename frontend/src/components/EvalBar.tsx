interface Props {
  cp: number | null;
  mate: number | null;
  orientation?: "white" | "black";
  height?: number;
}

export default function EvalBar({
  cp,
  mate,
  orientation = "white",
  height = 480,
}: Props) {
  // Calculate white's advantage as a percentage of the bar.
  let whitePct: number;
  let label: string;

  if (mate !== null && mate !== undefined) {
    if (mate > 0) {
      whitePct = 98;
      label = `M${mate}`;
    } else if (mate < 0) {
      whitePct = 2;
      label = `M${Math.abs(mate)}`;
    } else {
      whitePct = 50;
      label = "M0";
    }
  } else if (cp !== null && cp !== undefined) {
    // Map centipawns to percentage: ±500cp maps to 5%-95%.
    whitePct = Math.max(2, Math.min(98, 50 + cp / 10));
    const pawns = Math.abs(cp / 100);
    label = pawns >= 10 ? `${pawns.toFixed(0)}` : `${pawns.toFixed(1)}`;
    if (cp > 0) label = `+${label}`;
    else if (cp < 0) label = `-${label}`;
    else label = "0.0";
  } else {
    whitePct = 50;
    label = "—";
  }

  // If viewing from black's perspective, flip the bar.
  const displayPct = orientation === "white" ? whitePct : 100 - whitePct;

  // Determine if the advantage label should be light or dark text.
  const isWhiteAdvantage = (cp !== null && cp > 0) || (mate !== null && mate > 0);

  return (
    <div className="eval-bar" style={{ height }}>
      <div
        className="eval-bar__white"
        style={{ height: `${displayPct}%` }}
      />
      <div
        className="eval-bar__black"
        style={{ height: `${100 - displayPct}%` }}
      />
      <div
        className={`eval-bar__label ${
          isWhiteAdvantage ? "eval-bar__label--dark" : "eval-bar__label--light"
        }`}
      >
        {label}
      </div>
    </div>
  );
}
