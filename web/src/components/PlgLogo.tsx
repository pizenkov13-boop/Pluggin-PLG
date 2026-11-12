import logoSvg from "../../../assets/logo.svg?raw";

type Props = {
  className?: string;
  title?: string;
  /** Crop the source canvas to the mark's real bounds for narrow UI chrome. */
  compact?: boolean;
};

const VIEWBOX = logoSvg.match(/viewBox="([^"]+)"/)?.[1] ?? "0 0 600 400";
// The authored SVG has a 600×400 artboard while the actual PLG drawing sits
// roughly within x=72..550 / y=153..373.  A narrow sidebar must use the mark,
// not spend most of its 52px rail on that exported whitespace.
const COMPACT_VIEWBOX = "58 140 510 248";
// The mark's 3 solid letterform fills and 46 fine strokes (the checkmark-tail
// flourish under the G) each carry their own fill/stroke attributes in
// assets/logo.svg — a `d`-only extraction (the previous approach) drops
// those, silently rendering every stroke path as an unwanted filled blob
// instead of a thin line. Pulling the inner markup wholesale keeps each
// path exactly as authored; assets/logo.svg is a build-time asset we
// generate ourselves, not user input, so this is safe.
const INNER = logoSvg.replace(/^<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "");

/** Inline SVG from assets/logo.svg (generated from new.logo.svg). */
export default function PlgLogo({ className, title = "PLG", compact = false }: Props) {
  return (
    <svg
      className={className}
      viewBox={compact ? COMPACT_VIEWBOX : VIEWBOX}
      fill="currentColor"
      role="img"
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
      // eslint-disable-next-line react/no-danger -- trusted build-time SVG asset, see INNER comment above
      dangerouslySetInnerHTML={{ __html: INNER }}
    />
  );
}
