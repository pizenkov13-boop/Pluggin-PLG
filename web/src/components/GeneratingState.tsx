/* The one moment the product is visibly "working": a vertical equalizer of
   bars breathing on slow, staggered, organic cycles — not a hard pulse.
   Motion cites rickowens.eu.md directly: its Component Tokens list
   `transform 0.7s` / `filter 0.3s linear` / `top 0.7s` as the site's
   observed transition set, and its Taste DNA names this "Slow, Filter-
   Driven Motion as an Atmospheric Signature." Every bar's height animates
   on ~0.7s-class durations, and the whole stage carries a slow `filter:
   brightness()` breathing pulse using that exact CSS property — adapted
   from Rick Owens' one-time load transition into a continuous loop, since
   this screen needs an ongoing atmosphere rather than a single reveal.
   Achromatic; motion carries "alive", never hue. Display-only, no logic. */

import { motion, useReducedMotion } from "motion/react";
import "./GeneratingState.css";

type Props = {
  detail: string;
};

/** Pull seconds + phase from status strings like "Generating · 12s · arranging". */
function parseGeneratingDetail(detail: string): { secs: string | null; phase: string; tail: string } {
  const secsMatch = detail.match(/·\s*(\d+)\s*\S+\s*·/u);
  const secs = secsMatch?.[1] ?? null;

  let phase = detail;
  let tail = "";

  if (secsMatch) {
    const afterSecs = detail.slice(secsMatch.index! + secsMatch[0].length).trim();
    const dashIdx = afterSecs.indexOf(" — ");
    if (dashIdx >= 0) {
      phase = afterSecs.slice(0, dashIdx).trim();
      tail = afterSecs.slice(dashIdx + 3).trim();
    } else {
      phase = afterSecs;
    }
  }

  return { secs, phase, tail };
}

const BAR_COUNT = 28;
// Deterministic per-bar height/phase/duration so the equalizer looks
// intentional and organic (not random noise, not a uniform metronome).
const BARS = Array.from({ length: BAR_COUNT }, (_, i) => {
  const base = 0.35 + 0.5 * Math.abs(Math.sin(i * 0.7));
  return {
    min: Math.max(0.16, base - 0.28),
    max: Math.min(1, base + 0.24),
    duration: 2.6 + (i % 5) * 0.32, // 2.6s–3.9s — slow, Rick Owens class
    delay: (i % 7) * 0.09,
  };
});

export default function GeneratingState({ detail }: Props) {
  const reduce = useReducedMotion();
  // Keep seconds for the timer; drop phase labels (mix / arrange / …).
  const { secs } = parseGeneratingDetail(detail);

  return (
    <section className="generating" aria-live="polite" aria-busy="true">
      {secs != null && <p className="generating__secs">{secs}</p>}

      <motion.div
        className="generating__stage"
        aria-hidden
        animate={reduce ? undefined : { filter: ["brightness(0.92)", "brightness(1.12)", "brightness(0.92)"] }}
        transition={reduce ? undefined : { duration: 3.4, repeat: Infinity, ease: "easeInOut" }}
      >
        {BARS.map((bar, i) =>
          reduce ? (
            <span key={i} className="generating__bar" style={{ height: `${Math.round(bar.max * 100)}%` }} />
          ) : (
            <motion.span
              key={i}
              className="generating__bar"
              animate={{ scaleY: [bar.min, bar.max, bar.min], opacity: [0.5, 1, 0.5] }}
              transition={{ duration: bar.duration, delay: bar.delay, repeat: Infinity, ease: "easeInOut" }}
            />
          ),
        )}
      </motion.div>
    </section>
  );
}
