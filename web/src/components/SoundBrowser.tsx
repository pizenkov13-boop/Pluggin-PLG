import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Play, X } from "lucide-react";
import { listSounds, pinSound, unpinSound, type SoundEntry } from "../api";
import { apiErrorMessage } from "../errors";
import { useI18n } from "../i18n";
import { SoundPlayer } from "../soundPlayer";
import "./SoundBrowser.css";

/** The six cells, in the order the producer reads a kit. Lane names are the backend's. */
export const SOUND_CELLS = ["kick", "808_bass", "snare", "snare_layer", "clap", "hi_hats"] as const;
type Cell = (typeof SOUND_CELLS)[number];

const DRAG_TYPE = "application/x-plg-sound";
const SLIDE = { type: "spring" as const, stiffness: 340, damping: 38, mass: 0.9 };

type Props = {
  open: boolean;
  onClose: () => void;
  /** Lane -> file name, from the status (what the generation will use). */
  pinned: Record<string, string>;
  disabled: boolean;
  /** Re-read the status after a cell changed. */
  onChanged: () => Promise<void> | void;
  onBackToAuto: () => Promise<void> | void;
};

function previewUrl(base: string, rel: string): string {
  return base + rel.split("/").map(encodeURIComponent).join("/");
}

/** "Your sounds": every file of the user's library as a plain row (like FL's channel rack), a click
    plays it, and six cells (Kick, 808, Snare, Snare Layer, Clap, Hi-Hat) take the files the generation
    must use. An empty cell is matched automatically as before. */
export default function SoundBrowser({ open, onClose, pinned, disabled, onChanged, onBackToAuto }: Props) {
  const { t } = useI18n();
  const reduce = useReducedMotion();
  const [sounds, setSounds] = useState<SoundEntry[]>([]);
  const [previewBase, setPreviewBase] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [dropOver, setDropOver] = useState<Cell | null>(null);
  const playerRef = useRef<SoundPlayer | null>(null);
  const failedText = useRef("");
  failedText.current = t("sounds.playFailed");

  const stop = useCallback(() => {
    playerRef.current?.stop();
    setPlaying(null);
  }, []);

  // Read the library when the panel opens; stop any sound when it closes.
  useEffect(() => {
    if (!open) {
      stop();
      return;
    }
    let alive = true;
    setLoading(true);
    setMessage(null);
    listSounds()
      .then((result) => {
        if (!alive) return;
        if (!result.ok) {
          setMessage(t("sounds.loadFailed"));
          return;
        }
        setSounds(result.sounds ?? []);
        setPreviewBase(result.preview_base ?? "");
      })
      .catch(() => alive && setMessage(t("sounds.loadFailed")))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [open, stop, t]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => stop, [stop]);

  function play(rel: string) {
    const entry = sounds.find((sound) => sound.rel === rel);
    if (entry && entry.usable === false) {
      stop();
      setSelected(null);
      setMessage(t("sounds.unusable"));
      return;
    }
    setSelected(rel);
    if (!previewBase) {
      setMessage(t("sounds.noPreview"));
      return;
    }
    if (!playerRef.current) {
      playerRef.current = new SoundPlayer({
        onEnded: () => setPlaying(null),
        onFailed: () => {
          setPlaying(null);
          setMessage(failedText.current);
        },
      });
    }
    setMessage(null);
    setPlaying(rel);
    playerRef.current.play(previewUrl(previewBase, rel));
  }

  async function assign(cell: Cell, rel: string) {
    if (disabled) return;
    setMessage(null);
    try {
      const result = await pinSound(cell, rel);
      if (!result.ok) {
        const known = ["not_found", "outside_library", "unreadable", "unsupported_format"];
        setMessage(known.includes(result.error_type ?? "") ? t(`sounds.errors.${result.error_type}`) : apiErrorMessage(result, t));
        return;
      }
      await onChanged();
    } catch {
      setMessage(t("errors.somethingWentWrong"));
    }
  }

  async function clearCell(cell: Cell) {
    if (disabled) return;
    setMessage(null);
    try {
      const result = await unpinSound(cell);
      if (!result.ok) {
        setMessage(apiErrorMessage(result, t));
        return;
      }
      await onChanged();
    } catch {
      setMessage(t("errors.somethingWentWrong"));
    }
  }

  function onRowDragStart(event: DragEvent<HTMLElement>, rel: string) {
    event.dataTransfer.setData(DRAG_TYPE, rel);
    event.dataTransfer.effectAllowed = "copy";
  }

  function onCellDragOver(event: DragEvent<HTMLElement>, cell: Cell) {
    if (disabled || !event.dataTransfer.types.includes(DRAG_TYPE)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDropOver(cell);
  }

  function onCellDrop(event: DragEvent<HTMLElement>, cell: Cell) {
    const rel = event.dataTransfer.getData(DRAG_TYPE);
    setDropOver(null);
    if (!rel) return;
    // Our own drag: the Home page's file-import drop and the shell's folder drop must not see it.
    event.preventDefault();
    event.stopPropagation();
    void assign(cell, rel);
  }

  const hasPins = Object.keys(pinned).length > 0;
  const cellName = (cell: Cell) => t(`sounds.cells.${cell}`);

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          className="sound-browser"
          role="dialog"
          aria-label={t("sounds.title")}
          initial={reduce ? false : { x: "100%", opacity: 0.4 }}
          animate={{ x: 0, opacity: 1 }}
          exit={reduce ? { opacity: 0 } : { x: "100%", opacity: 0.4 }}
          transition={SLIDE}
        >
          <header className="sound-browser__head">
            <h2 className="sound-browser__title">{t("sounds.title")}</h2>
            <button type="button" className="sound-browser__close" onClick={onClose} aria-label={t("sounds.close")}>
              <X size={18} aria-hidden />
            </button>
          </header>

          <div className="sound-browser__cells" role="group" aria-label={t("sounds.cellsAria")}>
            {SOUND_CELLS.map((cell) => {
              const name = pinned[cell];
              return (
                <div
                  key={cell}
                  className={`sound-cell${name ? " sound-cell--set" : ""}${dropOver === cell ? " sound-cell--over" : ""}`}
                  onDragOver={(event) => onCellDragOver(event, cell)}
                  onDragLeave={() => setDropOver((current) => (current === cell ? null : current))}
                  onDrop={(event) => onCellDrop(event, cell)}
                >
                  <span className="sound-cell__label">{cellName(cell)}</span>
                  <span className="sound-cell__value" title={name ?? t("sounds.cellAutoHint")}>
                    {name ?? t("sounds.cellAuto")}
                  </span>
                  {name && (
                    <button
                      type="button"
                      className="sound-cell__clear"
                      onClick={() => void clearCell(cell)}
                      disabled={disabled}
                      aria-label={t("sounds.clearCell", { cell: cellName(cell) })}
                      title={t("sounds.clearCell", { cell: cellName(cell) })}
                    >
                      <X size={14} aria-hidden />
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          <p className="sound-browser__hint">{t("sounds.hint")}</p>
          {message && (
            <p className="sound-browser__message" role="status">
              {message}
            </p>
          )}

          <ul className="sound-browser__list" aria-label={t("sounds.listAria")}>
            {loading && <li className="sound-browser__note">{t("sounds.loading")}</li>}
            {!loading && sounds.length === 0 && <li className="sound-browser__note">{t("sounds.empty")}</li>}
            {sounds.map((sound) => {
              const isSelected = selected === sound.rel;
              const usable = sound.usable !== false;
              return (
                <li
                  key={sound.rel}
                  className={`sound-row${isSelected ? " sound-row--selected" : ""}${playing === sound.rel ? " sound-row--playing" : ""}${usable ? "" : " sound-row--unusable"}`}
                >
                  <button
                    type="button"
                    className="sound-row__main"
                    draggable={!disabled && usable}
                    onDragStart={(event) => onRowDragStart(event, sound.rel)}
                    onClick={() => play(sound.rel)}
                    title={usable ? sound.rel : t("sounds.unusable")}
                    aria-disabled={!usable}
                  >
                    <Play size={12} aria-hidden className="sound-row__icon" />
                    <span className="sound-row__name">{sound.name}</span>
                  </button>
                  {isSelected && (
                    <div className="sound-row__assign" role="group" aria-label={t("sounds.assignAria")}>
                      {SOUND_CELLS.map((cell) => (
                        <button
                          key={cell}
                          type="button"
                          className={`sound-row__chip${pinned[cell] === sound.name ? " sound-row__chip--on" : ""}`}
                          onClick={() => void assign(cell, sound.rel)}
                          disabled={disabled}
                        >
                          {cellName(cell)}
                        </button>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>

          <footer className="sound-browser__foot">
            <button
              type="button"
              className="sound-browser__auto"
              onClick={() => void onBackToAuto()}
              disabled={disabled || !hasPins}
            >
              {t("sounds.backToAuto")}
            </button>
          </footer>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
