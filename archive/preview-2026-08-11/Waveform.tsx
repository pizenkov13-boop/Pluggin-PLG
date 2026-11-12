/* Thin white bars on black — an audio monitor / equalizer.
   `animated` pulses the bars like a studio loading; otherwise it renders a
   still waveform for the result state. Pure visual, monochrome. */

import "./Waveform.css";

type Props = {
  bars?: number;
  animated?: boolean;
  className?: string;
};

// Deterministic "waveform" silhouette so the still monitor looks intentional.
const SHAPE = [
  0.22, 0.4, 0.32, 0.62, 0.48, 0.8, 0.58, 1, 0.72, 0.9, 0.55, 0.74, 0.42, 0.6,
  0.34, 0.5, 0.28, 0.44, 0.36, 0.66, 0.5, 0.82, 0.6, 0.94, 0.7, 0.86, 0.52, 0.7,
  0.4, 0.56, 0.3, 0.46, 0.26, 0.42, 0.34, 0.58, 0.46, 0.72, 0.54, 0.64,
];

export default function Waveform({ bars = 40, animated = false, className }: Props) {
  const items = Array.from({ length: bars }, (_, i) => SHAPE[i % SHAPE.length]);
  return (
    <div className={`waveform ${animated ? "waveform--live" : ""} ${className ?? ""}`} aria-hidden>
      {items.map((h, i) => (
        <span
          key={i}
          className="waveform__bar"
          style={{
            // still height for the monitor; animation overrides when live
            height: `${Math.round(h * 100)}%`,
            animationDelay: `${(i % 13) * 90}ms`,
          }}
        />
      ))}
    </div>
  );
}
