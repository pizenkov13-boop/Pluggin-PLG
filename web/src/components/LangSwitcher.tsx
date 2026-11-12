import { useI18n } from "../i18n";
import "./LangSwitcher.css";

/** RU⇄EN toggle — click swaps directly to the other language, no menu.
    If `locale` somehow holds anything else (stale localStorage from before
    this was a full dropdown), `current` still reflects it and `target`
    resolves to "ru", so the very next click recovers into the supported
    pair rather than getting stuck on a locale this control no longer
    offers a way to reach. */
export default function LangSwitcher() {
  const { locale, setLocale, localeOptions } = useI18n();
  const current = locale;
  const target = locale === "ru" ? "en" : "ru";
  const currentLabel = localeOptions.find((o) => o.id === current)?.label ?? current;
  const targetLabel = localeOptions.find((o) => o.id === target)?.label ?? target;

  return (
    <button
      type="button"
      className="lang"
      onClick={() => setLocale(target)}
      aria-label={`${currentLabel} → ${targetLabel}`}
    >
      <span className="lang__current">{current.toUpperCase()}</span>
      <span className="lang__name">{currentLabel}</span>
    </button>
  );
}
