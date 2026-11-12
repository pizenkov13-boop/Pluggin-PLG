import { useEffect, useState } from "react";
import { getDocs, openDocument, ready } from "../api";
import { useI18n } from "../i18n";
import { faqForLocale } from "../i18n/faq";
import "./PageView.css";

const LEGAL_DOC_IDS = ["terms", "privacy", "refund"] as const;
const SUPPORT_EMAIL = "help@example.com";

export default function HelpView() {
  const { t, locale, messages } = useI18n();
  const [docs, setDocs] = useState<Record<string, string> | null>(null);
  const [openFaq, setOpenFaq] = useState<string | null>(null);
  const [emailCopied, setEmailCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // A dedicated, doc-existence-only bridge call — not the general
    // get_app_info(), which also does FL detection and a real network
    // fetch for quota. That was adding a visible, avoidable delay before
    // this card could appear.
    ready().then(() => {
      getDocs().then((result) => setDocs(result.docs ?? {}));
    });
  }, []);

  async function onOpenDoc(id: string) {
    setError(null);
    const result = await openDocument(id);
    if (!result.ok) setError(result.error ?? t("help.openDocFailed"));
  }

  async function onCopySupportEmail() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(SUPPORT_EMAIL);
      } else {
        const input = document.createElement("textarea");
        input.value = SUPPORT_EMAIL;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.append(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      setEmailCopied(true);
      window.setTimeout(() => setEmailCopied(false), 1600);
    } catch {
      setError(SUPPORT_EMAIL);
    }
  }

  const legalDocs = LEGAL_DOC_IDS.filter((id) => docs?.[id]);
  const faqItems = faqForLocale(locale);

  return (
    <div className="page page--figma page--figma-help">
      <div className="page__head">
        <h1>{t("help.title")}</h1>
        <p>{t("help.desc")}</p>
      </div>

      <div className="page__card page__card--hero page__card--figma page__card--figma-help">
        <h2 className="page__card-title">{t("help.faq")}</h2>
        <ul className="page__list page__faq">
          {faqItems.map((item) => {
            const open = openFaq === item.id;
            return (
              <li key={item.id} className="page__faq-item">
                <button
                  type="button"
                  className="page__faq-q"
                  aria-expanded={open}
                  onClick={() => setOpenFaq(open ? null : item.id)}
                >
                  <span>{item.q}</span>
                  <span className={`page__faq-chevron ${open ? "page__faq-chevron--open" : ""}`} aria-hidden>
                    ▾
                  </span>
                </button>
                {open && <p className="page__faq-a">{item.a}</p>}
              </li>
            );
          })}
        </ul>
      </div>

      <div className="page__card page__card--hero page__card--figma page__card--figma-help">
        <h2 className="page__card-title">{t("help.support")}</h2>
        <p className="page__card-desc">{t("help.supportDesc")}</p>
        <div className="page__row">
          <button
            type="button"
            className="page__btn page__btn--ghost help__support-email"
            onClick={onCopySupportEmail}
            aria-label="Copy support email"
          >
            {emailCopied ? "Copied" : SUPPORT_EMAIL}
          </button>
        </div>
      </div>

      {legalDocs.length > 0 && (
        <div className="page__card page__card--hero page__card--figma page__card--figma-help">
          <h2 className="page__card-title">{t("help.legal")}</h2>
          <p className="page__card-desc">{t("help.legalDesc")}</p>
          <ul className="page__list">
            {legalDocs.map((id) => {
              const doc = messages.help.docs[id as keyof typeof messages.help.docs];
              if (!doc) return null;
              return (
                <li key={id} className="page__list-item legal__doc-bar">
                  <strong>{doc.title}</strong>
                  <button type="button" className="page__btn page__btn--ghost legal__open-btn" onClick={() => onOpenDoc(id)}>
                    {t("common.open")}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {error && <p className="page__error">{error}</p>}

      <p className="help__trademark">
        FL Studio and Image-Line are trademarks of Image-Line NV. Pluggin' (PLG) is not affiliated
        with or endorsed by Image-Line NV.
      </p>
    </div>
  );
}

