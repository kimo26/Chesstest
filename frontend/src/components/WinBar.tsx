/** Horizontal stacked bar showing white / draw / black win percentages. */
interface Props {
  white: number;
  draws: number;
  black: number;
}

export default function WinBar({ white, draws, black }: Props) {
  const total = white + draws + black || 1;
  const wPct = ((white / total) * 100).toFixed(1);
  const dPct = ((draws / total) * 100).toFixed(1);
  const bPct = ((black / total) * 100).toFixed(1);

  return (
    <div className="winbar" title={`W ${wPct}% D ${dPct}% B ${bPct}%`}>
      <div className="winbar__w" style={{ width: `${wPct}%` }}>
        {+wPct > 8 && `${wPct}%`}
      </div>
      <div className="winbar__d" style={{ width: `${dPct}%` }}>
        {+dPct > 8 && `${dPct}%`}
      </div>
      <div className="winbar__b" style={{ width: `${bPct}%` }}>
        {+bPct > 8 && `${bPct}%`}
      </div>
    </div>
  );
}
