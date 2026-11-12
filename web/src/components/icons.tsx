/* Sidebar + UI icons.

   The six nav-rail icons come from lucide-react (24×24, stroke-width 1.5) —
   they're re-exported under the original Icon* names so Sidebar.tsx and any
   future consumer keeps its import untouched. Everything below them is still
   hand-drawn at 20×20 / 1.75 stroke: those are app-specific glyphs (waveform,
   FL bridge, stem bake, chaos roll) with no lucide equivalent. */

import {
  CircleUserRound,
  Folder,
  House,
  Info,
  Settings,
  SquareLibrary,
  TriangleAlert,
  WifiOff,
  Wrench,
} from "lucide-react";

const NAV_ICON = {
  size: 24,
  strokeWidth: 1.5,
  "aria-hidden": true,
} as const;

/** Home. */
export function IconHome() {
  return <House {...NAV_ICON} />;
}

/** Sample library. */
export function IconLibrary() {
  return <SquareLibrary {...NAV_ICON} />;
}

/** Help / info. */
export function IconHelp() {
  return <Info {...NAV_ICON} />;
}

/** Producer tools. */
export function IconTools() {
  return <Wrench {...NAV_ICON} />;
}

/** Settings. */
export function IconSettings() {
  return <Settings {...NAV_ICON} />;
}

/** Account / profile. */
export function IconAccount() {
  return <CircleUserRound {...NAV_ICON} />;
}

const S = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none" as const,
  "aria-hidden": true,
};

const stroke = {
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function IconRegenerate() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 12a8 8 0 0 1 13.7-5.6M20 7V4m0 0h-3m3 0 5.2 5.2M20 12a8 8 0 0 1-13.7 5.6M4 17v3m0 0h3m-3 0-5.2-5.2"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function IconWave() {
  return (
    <svg {...S}>
      <path
        {...stroke}
        d="M3 10v4M7 8v8M11 6v12M15 9v6M19 7v10M23 10v4"
      />
    </svg>
  );
}

export function IconPlay() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M8 5.5v13l11-6.5-11-6.5Z" />
    </svg>
  );
}

export function IconFl() {
  return (
    <svg {...S}>
      <rect {...stroke} x="4.5" y="4.5" width="15" height="15" rx="3.5" />
      <path {...stroke} d="M8.5 9h7M8.5 12h5.5M8.5 15h4" />
    </svg>
  );
}

export function IconFolder() {
  return <Folder size={20} strokeWidth={1.75} aria-hidden />;
}

export function IconGrid() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="6" cy="6" r="1.6" fill="currentColor" />
      <circle cx="12" cy="6" r="1.6" fill="currentColor" />
      <circle cx="18" cy="6" r="1.6" fill="currentColor" />
      <circle cx="6" cy="12" r="1.6" fill="currentColor" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" />
      <circle cx="18" cy="12" r="1.6" fill="currentColor" />
      <circle cx="6" cy="18" r="1.6" fill="currentColor" />
      <circle cx="12" cy="18" r="1.6" fill="currentColor" />
      <circle cx="18" cy="18" r="1.6" fill="currentColor" />
    </svg>
  );
}

export function IconSearch() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.75" />
      <path d="M16 16l4 4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

type ThumbIconProps = {
  filled?: boolean;
};

export function IconThumbsUp({ filled = false }: ThumbIconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="1.75"
        fill={filled ? "currentColor" : "none"}
        fillOpacity={filled ? 0.14 : 0}
      />
      <path
        d="M8 12.5 10.75 15.25 16.25 9"
        stroke="currentColor"
        strokeWidth="1.85"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function IconThumbsDown({ filled = false }: ThumbIconProps) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="1.75"
        fill={filled ? "currentColor" : "none"}
        fillOpacity={filled ? 0.14 : 0}
      />
      <path
        d="M9 9l6 6M15 9l-6 6"
        stroke="currentColor"
        strokeWidth="1.85"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function IconWifiOff() {
  return <WifiOff size={20} strokeWidth={1.75} aria-hidden />;
}

/** Inline error/warning state (e.g. hero unsupported-file notice). */
export function IconWarning() {
  return <TriangleAlert size={18} strokeWidth={1.75} aria-hidden />;
}

/** Producer console — layered stem export. */
export function IconBakeStems() {
  return (
    <svg {...S}>
      <path {...stroke} d="M12 4 4 8.5 12 13l8-4.5L12 4Z" />
      <path {...stroke} d="M4 15.5 12 20l8-4.5" />
    </svg>
  );
}

/** Producer console — flip / mirror the beat. */
export function IconFlipBeat() {
  return (
    <svg {...S}>
      <path {...stroke} d="M8 7H3M5 3 3 7l2 4M5 21l-2-4 2-4" />
      <path {...stroke} d="M16 17h5M19 21l2-4-2-4M19 3l2 4-2 4" />
    </svg>
  );
}

/** Producer console — chaotic hat rolls. */
export function IconChaosRoll() {
  return (
    <svg {...S}>
      <path {...stroke} d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}

/** Producer console — master soft clipper. */
export function IconSoftClip({ active = false }: { active?: boolean }) {
  return (
    <svg {...S}>
      <path {...stroke} d="M3 9h18" strokeWidth={active ? 2.25 : 1.75} />
      <path
        {...stroke}
        d="M4 16c1.8 0 2.2-5 3.8-5s2 5 3.8 5 2.2-5 3.8-5 2 5 3.8 5"
      />
    </svg>
  );
}
