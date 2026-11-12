import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { importKitFolder, pickFolder, ready, scanLibrary } from "../api";
import type { ApiResult } from "../types";
import { useI18n } from "../i18n";
import { apiErrorMessage } from "../errors";
import "./PageView.css";

type ScanData = ApiResult & {
  root?: string;
  total?: number;
  audio_total?: number;
  audio?: Record<string, string[]>;
  midi?: string[];
};

// Legacy, unmounted screen kept only for source-history compatibility. Home
// owns material intake now: users drag MIDI and kit files directly there.
export default function LibraryView() {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ready().finally(() => setLoading(false));
  }, []);

  async function onScan() {
    const result = (await scanLibrary()) as ScanData;
    if (!result.ok) {
      setError(apiErrorMessage(result, t));
    }
  }

  // Both "OPEN DRUM KITS" and "OPEN MIDI" (Figma node 1:37) drive this one
  // import flow — scan_library sorts midi/audio by extension after the fact.
  async function onImport() {
    setError(null);
    const pick = await pickFolder();
    if (!pick.ok) {
      if (!pick.cancelled) setError(apiErrorMessage(pick, t));
      return;
    }
    if (!pick.path) return;

    setImporting(true);
    const result = await importKitFolder(pick.path, false);
    setImporting(false);
    if (!result.ok) {
      setError(apiErrorMessage(result, t));
      return;
    }
    await onScan();
  }

  if (loading) {
    return <div className="page page--loading">{t("common.loading")}</div>;
  }

  return (
    <motion.div
      className="page page--figma page--figma-library"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 320, damping: 34, mass: 0.9 }}
    >
      <div className="page__head">
        <h1>{t("library.title")}</h1>
        <p>{t("library.desc")}</p>
      </div>

      <div className="page__card page__card--hero page__card--figma page__card--figma-row page__card--figma-library">
        <button type="button" className="page__hero-btn" onClick={onImport} disabled={importing}>
          {importing ? t("library.importing") : t("library.openDrumKits")}
        </button>
        <button type="button" className="page__hero-btn" onClick={onImport} disabled={importing}>
          {importing ? t("library.importing") : t("library.openMidi")}
        </button>
      </div>

      {error && <p className="page__error">{error}</p>}
    </motion.div>
  );
}
