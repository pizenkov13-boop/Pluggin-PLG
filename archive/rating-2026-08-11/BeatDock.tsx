import { useEffect, useMemo, useState } from "react";
import type { BeatResult, Status } from "../types";
import { recordBeatRating } from "../api";
import { useI18n } from "../i18n";
import { IconFl, IconRegenerate, IconThumbsDown, IconThumbsUp } from "./icons";
import "./BeatDock.css";

type Props = {
  status: Status | null;
  lastBeat: BeatResult | null;
  busy: boolean;
  onOpenInFl: () => void | Promise<void>;
  onRegenerate?: () => void;
  onRated?: () => void;
};

export default function BeatDock({
  status,
  lastBeat,
  busy,
  onOpenInFl,
  onRegenerate,
  onRated,
}: Props) {
  const { t } = useI18n();
  const [ratingBusy, setRatingBusy] = useState(false);
  const [rateMessage, setRateMessage] = useState<string | null>(null);
  const [flReminder, setFlReminder] = useState(false);
  const [localModelReady, setLocalModelReady] = useState<boolean | null>(null);
  const [localFavoriteGenre, setLocalFavoriteGenre] = useState<string | null>(null);
  const [localRatings, setLocalRatings] = useState<number | null>(null);

  const bpm = lastBeat?.bpm ?? status?.bpm;
  const style = lastBeat?.style ?? status?.style;
  const stemFiles = lastBeat?.stem_files?.length ? lastBeat.stem_files : status?.stem_files;
  const beatRating = lastBeat?.beat_rating ?? status?.beat_rating;
  const rewardRatings = localRatings ?? status?.reward_ratings ?? 0;
  const rewardMin = status?.reward_min_samples ?? 6;
  const rewardProgress = status?.reward_progress ?? 0;
  const modelReady = localModelReady ?? status?.reward_model_ready ?? false;
  const favoriteGenre = localFavoriteGenre ?? status?.reward_favorite_genre ?? null;
  const rated = beatRating === 1 || beatRating === -1;
  const beatReady = bpm != null || Boolean(lastBeat?.ok ?? status?.beat_ready);

  useEffect(() => {
    setFlReminder(false);
    setRateMessage(null);
  }, [bpm, style, beatRating]);

  const statusHint = useMemo(() => {
    if (rateMessage) return rateMessage;
    if (flReminder && !rated) return t("session.rateAfterFl");
    if (modelReady) {
      if (favoriteGenre) {
        return t("session.rateFavoriteGenre", { genre: favoriteGenre });
      }
      return t("session.rateModelActive");
    }
    if (rewardRatings > 0) {
      return t("session.rateCollecting", { current: rewardRatings, min: rewardMin });
    }
    return t("session.rateSubtitle");
  }, [
    rateMessage,
    flReminder,
    rated,
    modelReady,
    favoriteGenre,
    rewardRatings,
    rewardMin,
    t,
  ]);

  async function handleRate(rating: number) {
    if (rated || ratingBusy || busy) return;
    setRatingBusy(true);
    setRateMessage(null);
    try {
      const result = await recordBeatRating(rating);
      if (result.ok) {
        setRateMessage(t("session.rateThanks"));
        setFlReminder(false);
        if (typeof result.total_ratings === "number") setLocalRatings(result.total_ratings);
        if (typeof result.model_ready === "boolean") setLocalModelReady(result.model_ready);
        if (typeof result.favorite_genre === "string") setLocalFavoriteGenre(result.favorite_genre);
        onRated?.();
      } else {
        setRateMessage(result.error ?? t("session.rateFailed"));
      }
    } catch {
      setRateMessage(t("session.rateFailed"));
    } finally {
      setRatingBusy(false);
    }
  }

  async function handleOpenInFl() {
    if (!rated) setFlReminder(true);
    await onOpenInFl();
  }

  return (
    <section className="beat-dock" aria-label={t("home.yourBeat")}>
      <div className="beat-dock__head">
        <div className="beat-dock__ready">
          <span className="beat-dock__dot" aria-hidden />
          {t("session.beatReady")}
        </div>
        {(bpm != null || style) && (
          <div className="beat-dock__meta">
            {bpm != null && (
              <span>
                {t("session.bpm")} · {bpm}
              </span>
            )}
            {style && <span>{style}</span>}
          </div>
        )}
      </div>

      {beatReady && (
        <div
          className={`beat-dock__rate${modelReady ? " beat-dock__rate--live" : ""}${flReminder && !rated ? " beat-dock__rate--pulse" : ""}`}
          aria-label={t("session.rateBeat")}
        >
          <div className="beat-dock__rate-top">
            <div>
              <span className="beat-dock__rate-label">{t("session.rateBeat")}</span>
              <p className="beat-dock__rate-sub">{t("session.rateBeforeFl")}</p>
            </div>
            {modelReady && <span className="beat-dock__rate-badge">{t("session.rateBadgeLive")}</span>}
          </div>

          <div className="beat-dock__rate-actions">
            <button
              type="button"
              className={`beat-dock__rate-btn beat-dock__rate-btn--up${beatRating === 1 ? " beat-dock__rate-btn--active" : ""}`}
              onClick={() => handleRate(1)}
              disabled={busy || ratingBusy || rated}
              aria-pressed={beatRating === 1}
              title={t("session.rateUp")}
            >
              <IconThumbsUp filled={beatRating === 1} />
              <span>{t("session.rateUp")}</span>
            </button>
            <button
              type="button"
              className={`beat-dock__rate-btn beat-dock__rate-btn--down${beatRating === -1 ? " beat-dock__rate-btn--active" : ""}`}
              onClick={() => handleRate(-1)}
              disabled={busy || ratingBusy || rated}
              aria-pressed={beatRating === -1}
              title={t("session.rateDown")}
            >
              <IconThumbsDown filled={beatRating === -1} />
              <span>{t("session.rateDown")}</span>
            </button>
          </div>

          {!modelReady && rewardRatings > 0 && (
            <div className="beat-dock__rate-progress" aria-hidden>
              <div className="beat-dock__rate-progress-fill" style={{ width: `${Math.round(rewardProgress * 100)}%` }} />
            </div>
          )}

          <p className={`beat-dock__rate-hint${rateMessage ? " beat-dock__rate-hint--ok" : ""}${flReminder && !rated ? " beat-dock__rate-hint--remind" : ""}`}>
            {statusHint}
          </p>
        </div>
      )}

      <div className="beat-dock__grid">
        <button
          type="button"
          className="beat-dock__tile beat-dock__tile--hero"
          onClick={handleOpenInFl}
          disabled={busy}
        >
          <IconFl />
          <span className="beat-dock__tile-label">{t("home.openInFl")}</span>
        </button>

        {onRegenerate && (
          <button
            type="button"
            className="beat-dock__tile beat-dock__tile--quiet"
            onClick={onRegenerate}
            disabled={busy}
            title={t("regenerate.minusOne")}
          >
            <IconRegenerate />
            <span className="beat-dock__tile-label">{t("regenerate.button")}</span>
          </button>
        )}
      </div>

      {stemFiles && stemFiles.length > 0 && (
        <ul className="beat-dock__stems" aria-label={t("session.stemExport")}>
          {stemFiles.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
