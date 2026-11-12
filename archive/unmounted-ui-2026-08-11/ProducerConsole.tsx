import { useState } from "react";
import { chaosRoll, flipBeat, setClipperMode } from "../api";
import type { ApiResult } from "../types";
import { useI18n } from "../i18n";
import { apiErrorMessage } from "../errors";
import {
  IconChaosRoll,
  IconFlipBeat,
  IconSoftClip,
} from "./icons";
import "./ProducerConsole.css";

type Props = {
  beatReady: boolean;
  busy: boolean;
  clipperMode?: boolean;
  onUpdated: (result: ApiResult) => void;
  onError: (message: string) => void;
};

export default function ProducerConsole({
  beatReady,
  busy,
  clipperMode = false,
  onUpdated,
  onError,
}: Props) {
  const { t } = useI18n();
  const [toolBusy, setToolBusy] = useState(false);

  async function run(action: () => Promise<ApiResult>) {
    if (!beatReady || busy || toolBusy) return;
    setToolBusy(true);
    try {
      const result = await action();
      if (!result.ok) {
        onError(apiErrorMessage(result, t));
        return;
      }
      onUpdated(result);
    } finally {
      setToolBusy(false);
    }
  }

  const locked = !beatReady || busy || toolBusy;

  return (
    <section className="console">
      <div className="console__head">
        <h3>{t("console.title")}</h3>
        {clipperMode && (
          <span className="console__clipper-banner" aria-live="polite">
            {t("console.clipperBanner")}
          </span>
        )}
      </div>

      <div className="console__grid">
        <button
          type="button"
          className="console__btn"
          disabled={locked}
          onClick={() => run(flipBeat)}
        >
          <span className="console__icon" aria-hidden>
            <IconFlipBeat />
          </span>
          <span>{t("console.flip")}</span>
        </button>

        <button
          type="button"
          className="console__btn"
          disabled={locked}
          onClick={() => run(chaosRoll)}
        >
          <span className="console__icon" aria-hidden>
            <IconChaosRoll />
          </span>
          <span>{t("console.chaos")}</span>
        </button>

        <button
          type="button"
          className={`console__btn console__btn--toggle ${clipperMode ? "console__btn--on" : ""}`}
          disabled={locked}
          onClick={() => run(() => setClipperMode(!clipperMode))}
          title={t("console.clipperTip")}
        >
          <span className="console__icon" aria-hidden>
            <IconSoftClip active={clipperMode} />
          </span>
          <span>{clipperMode ? t("console.clipperOn") : t("console.clipperOff")}</span>
        </button>
      </div>

      {toolBusy && <p className="console__hint">{t("console.applying")}</p>}
      {!beatReady && <p className="console__hint">{t("console.needBeat")}</p>}
    </section>
  );
}
