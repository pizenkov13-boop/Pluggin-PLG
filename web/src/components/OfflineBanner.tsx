import { useI18n } from "../i18n";
import { IconWifiOff } from "./icons";
import "./OfflineBanner.css";

type Props = {
  online: boolean;
  cloudMode?: boolean;
  cloudUrl?: string | null;
  retrying?: boolean;
  onRetry?: () => void;
};

export default function OfflineBanner({ online, cloudMode, cloudUrl, retrying, onRetry }: Props) {
  const { t } = useI18n();
  if (!cloudMode || online) return null;

  return (
    <div className="offline-banner" role="status">
      <div className="offline-banner__icon" aria-hidden>
        <IconWifiOff />
      </div>
      <div className="offline-banner__body">
        <strong>{t("offline.title")}</strong>
        <p className="offline-banner__desc">{t("offline.desc", { url: (cloudUrl ?? "").replace(/^https?:\/\//, "") || "PLG" })}</p>
        <div className="offline-banner__actions">
          <button
            type="button"
            className="offline-banner__btn offline-banner__btn--primary"
            onClick={onRetry}
            disabled={retrying}
          >
            {retrying ? t("offline.checking") : t("offline.retry")}
          </button>
        </div>
      </div>
    </div>
  );
}
