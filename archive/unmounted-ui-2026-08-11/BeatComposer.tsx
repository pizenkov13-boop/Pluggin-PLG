import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useI18n } from "../i18n";
import type { AttachedFile, DetectedGenre } from "../types";
import { IconFolder } from "./icons";
import "./BeatComposer.css";

type Props = {
  onCreate: () => void;
  busy: boolean;
  canCreate: boolean;
  error: string | null;
  hasMaterial: boolean;
  materialCount: number;
  attachedFile?: AttachedFile | null;
  onClearAttach?: () => void;
  onOpenLibrary?: () => void;
  respectUploadedScale?: boolean;
  onRespectUploadedScaleChange?: (value: boolean) => void;
  uploadNotice?: string | null;
  detectedGenre?: DetectedGenre | null;
  detectingGenre?: boolean;
  genreOverride?: string | null;
  onGenreOverrideChange?: (value: string | null) => void;
  availableGenres?: string[];
};

function humanizeGenre(genre: string): string {
  return genre
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

const ENTRANCE = { type: "spring" as const, bounce: 0, duration: 0.48 };
const INTERACT = { type: "spring" as const, stiffness: 320, damping: 34, mass: 0.9 };

const CTA_VARIANTS = {
  sleeping: {
    scale: 1,
    backgroundColor: "rgba(255, 255, 255, 0.035)",
    color: "rgba(250, 250, 250, 0.38)",
    borderColor: "rgba(255, 255, 255, 0.12)",
    boxShadow: "0 0 0 1px inset rgba(255,255,255,0.025), 0 0 0 rgba(255,255,255,0)",
  },
  armed: {
    scale: 1,
    backgroundColor: "rgba(250, 250, 250, 0.97)",
    color: "#050505",
    borderColor: "rgba(255, 255, 255, 0.96)",
    boxShadow:
      "0 0 0 1px inset rgba(255,255,255,0.72), 0 18px 54px -24px rgba(255,255,255,0.62), 0 0 42px -24px rgba(255,255,255,0.82)",
  },
};

function copy(locale: string) {
  const ru = locale === "ru";
  return {
    title: ru ? "Закинь MIDI и драм киты" : "Drop MIDI and drum kits",
    desc: ru
      ? "PLG найдёт материал в твоих папках и соберёт структуру бита"
      : "PLG finds material in your folders and builds the beat structure",
    sleeping: ru ? "Ожидаю материал" : "Waiting for material",
    scanning: ru ? "Материал обнаружен" : "Material detected",
    armed: ru ? "Создать бит" : "Create beat",
    libraryReady: ru ? "Библиотека готова" : "Library ready",
    libraryEmpty: ru ? "Библиотека ждёт звук" : "Library is waiting",
    dropReady: ru ? "Материал синхронизирован" : "Material synchronized",
    dropEmpty: ru ? "Добавь файлы в папки библиотеки" : "Add files to the library folders",
    readyHint: ru ? "PLG уже видит материал и готов к сборке" : "PLG sees the material and is ready to build",
    emptyHint: ru ? "MIDI, 808, кики, хэты и другие сэмплы" : "MIDI, 808s, kicks, hats and other samples",
    openLibrary: ru ? "Открыть библиотеку" : "Open library",
    audioCount: ru ? "{count} файлов" : "{count} files",
    roles: ["808", "Kick", "Hi-Hats", "Snare", "Clap", "Rimshot"],
  };
}

export default function BeatComposer({
  onCreate,
  busy,
  canCreate,
  error,
  hasMaterial,
  materialCount,
  attachedFile,
  onClearAttach,
  onOpenLibrary,
  respectUploadedScale,
  onRespectUploadedScaleChange,
  uploadNotice,
  detectedGenre,
  detectingGenre,
  genreOverride,
  onGenreOverrideChange,
  availableGenres,
}: Props) {
  const { locale, t } = useI18n();
  const reduce = useReducedMotion();
  const c = copy(locale);
  const ctaState = hasMaterial && canCreate && !busy ? "armed" : "sleeping";
  const ctaText = hasMaterial ? c.armed : c.sleeping;
  const materialLabel = attachedFile
    ? attachedFile.name
    : materialCount > 0
      ? c.audioCount.replace("{count}", String(materialCount))
      : c.libraryEmpty;

  const enter = reduce
    ? {}
    : { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 }, transition: ENTRANCE };

  return (
    <motion.section className={`composer ${hasMaterial ? "composer--armed" : "composer--sleeping"}`} {...enter}>
      <div className="composer__copy">
        <div className="composer__signal">
          <motion.span
            className="composer__signal-led"
            animate={hasMaterial ? { opacity: 1, scale: 1 } : { opacity: 0.34, scale: 0.82 }}
            transition={INTERACT}
            aria-hidden
          />
          <span>{hasMaterial ? c.scanning : c.sleeping}</span>
        </div>
        <h1 className="composer__title">{c.title}</h1>
        <p className="composer__desc">{c.desc}</p>
      </div>

      <motion.div
        className="composer__material"
        aria-live="polite"
        whileHover={reduce ? undefined : { y: -3, scale: 1.004 }}
        transition={INTERACT}
      >
        <div className="composer__material-head">
          <div className="composer__material-heading">
            <span className="composer__state-kicker">PLG / LIBRARY</span>
            <strong>{hasMaterial ? c.libraryReady : c.libraryEmpty}</strong>
          </div>
          <div className="composer__material-status" title={materialLabel}>
            <span>{materialLabel}</span>
          </div>
        </div>

        <div className="composer__core-stage">
          <div className="composer__orbits" aria-hidden>
            <span />
            <span />
            <span />
          </div>
          <motion.span
            className="composer__core-icon"
            animate={
              hasMaterial
                ? { opacity: 1, scale: 1, boxShadow: "0 0 42px rgba(255,255,255,0.14)" }
                : { opacity: 0.55, scale: 0.94, boxShadow: "0 0 0 rgba(255,255,255,0)" }
            }
            transition={INTERACT}
            aria-hidden
          >
            <IconFolder />
          </motion.span>
          <strong className="composer__core-title">{hasMaterial ? c.dropReady : c.dropEmpty}</strong>
          <span className="composer__core-detail">{hasMaterial ? c.readyHint : c.emptyHint}</span>
        </div>

        <div className="composer__roles" role="list" aria-label="Detected material roles">
          {c.roles.map((role, index) => (
            <motion.span
              key={role}
              role="listitem"
              className="composer__role"
              animate={hasMaterial ? { opacity: 1, y: 0 } : { opacity: 0.42, y: 0 }}
              transition={reduce ? undefined : { ...INTERACT, delay: hasMaterial ? index * 0.035 : 0 }}
              whileHover={reduce ? undefined : { y: -2, backgroundColor: "rgba(255,255,255,0.1)" }}
            >
              {role}
            </motion.span>
          ))}
        </div>
      </motion.div>

      <div className="composer__actions">
        <motion.button
          type="button"
          className={`composer__cta ${ctaState === "armed" ? "composer__cta--ready" : "composer__cta--sleeping"}`}
          onClick={onCreate}
          disabled={!canCreate || busy}
          variants={CTA_VARIANTS}
          initial={false}
          animate={ctaState}
          transition={INTERACT}
          whileHover={reduce || !canCreate || busy ? undefined : { y: -2, scale: 1.012, transition: INTERACT }}
          whileTap={reduce || !canCreate || busy ? undefined : { y: 0, scale: 0.985, transition: INTERACT }}
        >
          <span className="composer__cta-inner">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={ctaText}
                className="composer__cta-text"
                initial={reduce ? false : { opacity: 0, y: 7 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduce ? undefined : { opacity: 0, y: -7 }}
                transition={INTERACT}
              >
                {ctaText}
              </motion.span>
            </AnimatePresence>
            <span className="composer__cta-arrow" aria-hidden>
              <svg viewBox="0 0 20 20" fill="none">
                <path d="M4.5 10h10M10.5 6l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </span>
        </motion.button>

        {!hasMaterial && onOpenLibrary && (
          <button type="button" className="composer__library-link" onClick={onOpenLibrary}>
            {c.openLibrary}
          </button>
        )}
      </div>

      {attachedFile && (
        <div className="composer__attachment">
          <span className="composer__attachment-name">
            {t(attachedFile.kind === "midi" ? "home.attachedMidi" : "home.attachedReference", {
              name: attachedFile.name,
            })}
          </span>
          {onClearAttach && (
            <button type="button" className="composer__attachment-clear" onClick={onClearAttach}>
              {t("home.clearAttachment")}
            </button>
          )}
        </div>
      )}

      {attachedFile?.kind === "midi" &&
        (detectingGenre || detectedGenre || (availableGenres && availableGenres.length > 0)) && (
          <div className="composer__genre">
            <span className="composer__genre-label">
              {detectingGenre
                ? t("home.detectingGenre")
                : detectedGenre
                  ? t("home.detectedGenreLabel", {
                      genre: humanizeGenre(genreOverride ?? detectedGenre.genre),
                    })
                  : t("home.genreUnavailable")}
            </span>
            {onGenreOverrideChange && availableGenres && availableGenres.length > 0 && (
              <select
                className="composer__genre-select"
                aria-label={t("home.genrePickAria")}
                value={genreOverride ?? detectedGenre?.genre ?? ""}
                onChange={(event) => onGenreOverrideChange(event.target.value || null)}
              >
                {!detectedGenre && (
                  <option value="" disabled>
                    {t("home.genrePickPlaceholder")}
                  </option>
                )}
                {availableGenres.map((genre) => (
                  <option key={genre} value={genre}>
                    {humanizeGenre(genre)}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

      {attachedFile && onRespectUploadedScaleChange && (
        <label className="composer__scale-check">
          <input
            type="checkbox"
            checked={Boolean(respectUploadedScale)}
            onChange={(event) => onRespectUploadedScaleChange(event.target.checked)}
          />
          <span>{t("home.respectUploadedScale")}</span>
        </label>
      )}

      {uploadNotice && <div className="composer__notice" role="status">{uploadNotice}</div>}
      {error && <div className="composer__error" role="alert">{error}</div>}
    </motion.section>
  );
}
