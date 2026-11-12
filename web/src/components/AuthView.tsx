import { useEffect, useState } from "react";

import type { ApiResult } from "../types";
import {
  cloudCompletePasswordReset,
  cloudLogin,
  cloudResendVerification,
  cloudResetPassword,
  cloudSignup,
  cloudVerifyEmail,
  getAuthStatus,
} from "../api";

import { useI18n } from "../i18n";
import { apiErrorMessage } from "../errors";
import landingLogoUrl from "../../../landing/logo.svg?url";
import termsDocumentRu from "../../../legal/TERMS_RU.md?raw";
import privacyDocumentRu from "../../../legal/PRIVACY_RU.md?raw";
import termsDocumentEn from "../../../legal/TERMS_EN.md?raw";
import privacyDocumentEn from "../../../legal/PRIVACY_EN.md?raw";
import LangSwitcher from "./LangSwitcher";
import { IconWarning } from "./icons";
import "./AuthView.css";

type Props = {
  onAuthed: () => void;
};

type LegalDocumentId = "terms" | "privacy";

function renderLegalDocument(content: string) {
  return content
    .trim()
    .split(/\r?\n\s*\r?\n/)
    .filter(Boolean)
    .map((block, index) => {
      const lines = block.trim().split(/\r?\n/);
      const heading = lines[0].match(/^(#{1,3})\s+(.+)$/);
      const text = lines
        .map((line) => line.replace(/^[-*]\s+/, "• ").replace(/\*\*(.*?)\*\*/g, "$1"))
        .join(" ");
      if (heading) {
        const Heading = heading[1].length === 1 ? "h1" : "h2";
        return <Heading key={index}>{heading[2]}</Heading>;
      }
      return <p key={index}>{text}</p>;
    });
}

export default function AuthView({ onAuthed }: Props) {
  const { t, locale } = useI18n();
  // A fresh PLG install has no session, so registration is the useful first
  // action. Existing users still have a clear "already have an account" link.
  const [mode, setMode] = useState<"login" | "signup" | "verify" | "recovery">("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [verificationUserId, setVerificationUserId] = useState("");
  const [honeypot, setHoneypot] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [confirmAge, setConfirmAge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [legalDocument, setLegalDocument] = useState<LegalDocumentId | null>(null);
  const [resetCodeSent, setResetCodeSent] = useState(false);

  useEffect(() => {
    getAuthStatus().then((s) => {
      if (s.signed_in) onAuthed();
    });
    // Only on mount — onAuthed is stable enough for session restore.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit() {
    if (busy || honeypot.trim()) return;
    if (!email.trim()) {
      if (mode === "recovery") setError(t("auth.emailRequired"));
      return;
    }
    if (mode === "recovery") {
      if (!resetCodeSent) {
        setBusy(true);
        setError(null);
        const result = await cloudResetPassword(email.trim());
        setBusy(false);
        if (!result.ok) {
          setError(apiErrorMessage(result, t));
          return;
        }
        setResetCodeSent(true);
        setMessage(t("auth.resetSent"));
        return;
      }
      if (verificationCode.replace(/\D/g, "").length !== 6 || !password) return;
      if (password.length < 8) {
        setError(t("auth.passwordMin"));
        return;
      }
      setBusy(true);
      setError(null);
      setMessage(null);
      const result = await cloudCompletePasswordReset(email.trim(), verificationCode, password);
      setBusy(false);
      if (!result.ok) {
        setError(apiErrorMessage(result, t));
        return;
      }
      setVerificationCode("");
      setPassword("");
      setMode("login");
      setMessage(t("auth.passwordChanged"));
      return;
    }
    if (mode === "verify") {
      if (!verificationUserId || verificationCode.replace(/\D/g, "").length !== 6) return;
      setBusy(true);
      setError(null);
      setMessage(null);
      const verified = await cloudVerifyEmail(verificationUserId, verificationCode);
      if (!verified.ok) {
        setBusy(false);
        setError(apiErrorMessage(verified, t));
        return;
      }
      const login = await cloudLogin(email.trim(), password);
      setBusy(false);
      if (!login.ok) {
        setError(apiErrorMessage(login, t));
        return;
      }
      onAuthed();
      return;
    }
    if (!password) return;
    if (mode === "signup" && (!acceptTerms || !confirmAge)) {
      setError(t("auth.legalRequired"));
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);

    const result =
      mode === "login"
        ? await cloudLogin(email.trim(), password)
        : await cloudSignup(email.trim(), password, null, acceptTerms, confirmAge);

    setBusy(false);

    if (!result.ok) {
      setError(apiErrorMessage(result, t));
      return;
    }

    if (mode === "signup") {
      const verification = result as ApiResult & {
        verification_required?: boolean;
        verification_user_id?: string;
      };
      if (verification.verification_required && verification.verification_user_id) {
        setVerificationUserId(verification.verification_user_id);
        setVerificationCode("");
        setMode("verify");
        setMessage(null);
        return;
      }
      const sess = (result as ApiResult & { session?: { access_token?: string } }).session;
      const needsLogin = Boolean((result as ApiResult & { needs_login?: boolean }).needs_login);
      if (!sess?.access_token || needsLogin) {
        setMessage(t("auth.signupLogin"));
        setMode("login");
        return;
      }
    }

    onAuthed();
  }

  async function onResendCode() {
    if (!verificationUserId || busy) return;
    setBusy(true);
    setError(null);
    const result = await cloudResendVerification(verificationUserId);
    setBusy(false);
    if (!result.ok) {
      setError(apiErrorMessage(result, t));
      return;
    }
    setMessage(t("auth.codeResent"));
  }

  async function onForgot() {
    if (busy) return;
    setMode("recovery");
    setResetCodeSent(false);
    setVerificationCode("");
    setPassword("");
    setError(null);
    setMessage(null);
  }

  function returnToLogin() {
    setMode("login");
    setVerificationCode("");
    setPassword("");
    setResetCodeSent(false);
    setError(null);
    setMessage(null);
  }

  const termsDocument = locale === "ru" ? termsDocumentRu : termsDocumentEn;
  const privacyDocument = locale === "ru" ? privacyDocumentRu : privacyDocumentEn;
  const legalContent = legalDocument === "terms" ? termsDocument : privacyDocument;

  return (
    <div className="auth">
      <div className="auth__lang">
        <LangSwitcher />
      </div>
      <div className={`auth__panel ${legalDocument ? "auth__panel--legal" : ""}`}>
        <img className="auth__logo" src={landingLogoUrl} alt="PLG" />
        <div className={`auth__card ${legalDocument ? "auth__card--legal" : ""}`}>
        {legalDocument ? (
          <>
            <article className="auth__legal-document">
              {renderLegalDocument(legalContent)}
            </article>
            <button type="button" className="auth__link auth__legal-back" onClick={() => setLegalDocument(null)}>
              {t("auth.back")}
            </button>
          </>
        ) : (
          <>
        <div className="auth__copy">
          <h1>
            {mode === "verify"
              ? t("auth.verifyTitle")
              : mode === "recovery"
                ? t("auth.resetTitle")
                : mode === "login"
                  ? t("auth.title")
                  : t("auth.signupTitle")}
          </h1>
        </div>

        <input
          type="text"
          name="website"
          value={honeypot}
          onChange={(e) => setHoneypot(e.target.value)}
          tabIndex={-1}
          autoComplete="off"
          className="auth__honeypot"
          aria-hidden="true"
        />

        {mode === "verify" ? (
          <label className="auth__field">
            <span>{t("auth.verificationCode")}</span>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={verificationCode}
              onChange={(e) => setVerificationCode(e.target.value.replace(/\D/g, ""))}
            />
          </label>
        ) : mode === "recovery" ? (
          <>
            <label className="auth__field">
              <span>{t("auth.email")}</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                readOnly={resetCodeSent}
              />
            </label>
            {resetCodeSent && <label className="auth__field">
              <span>{t("auth.resetCode")}</span>
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={verificationCode}
                onChange={(e) => setVerificationCode(e.target.value.replace(/\D/g, ""))}
              />
            </label>}
            {resetCodeSent && <label className="auth__field">
              <span>{t("auth.newPassword")}</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={8}
                autoComplete="new-password"
              />
            </label>}
          </>
        ) : (
          <>
            <label className="auth__field">
              <span>{t("auth.email")}</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </label>

            <label className="auth__field">
              <span>{t("auth.password")}</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </label>
          </>
        )}

        {mode === "signup" && (
          // One "conditions" group, tightly spaced inside itself — see
          // .auth__terms. Proximity: these three lines are one idea (what
          // you're agreeing to), so they sit closer to each other than to
          // the field above or the button below.
          <div className="auth__terms">
            <label className="auth__check">
              <input
                type="checkbox"
                checked={acceptTerms}
                onChange={(e) => setAcceptTerms(e.target.checked)}
              />
              <span>
                {t("auth.acceptTermsPrefix")} {" "}
                <button
                  type="button"
                  className="auth__legal-link"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setLegalDocument("terms");
                  }}
                >
                  {t("auth.terms")}
                </button>{" "}
                {t("auth.acceptTermsMiddle")} {" "}
                <button
                  type="button"
                  className="auth__legal-link"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setLegalDocument("privacy");
                  }}
                >
                  {t("auth.privacy")}
                </button>
              </span>
            </label>
            <label className="auth__check">
              <input
                type="checkbox"
                checked={confirmAge}
                onChange={(e) => setConfirmAge(e.target.checked)}
              />
              <span>{t("auth.confirmAge")}</span>
            </label>
            <p className="auth__hint">{t("auth.aiDisclaimer")}</p>
          </div>
        )}

        {error && (
          <p className="auth__error">
            <IconWarning />
            <span>{error}</span>
          </p>
        )}
        {message && <p className="auth__success">{message}</p>}

        <button type="button" className="auth__btn" onClick={onSubmit} disabled={busy}>
          {busy
            ? mode === "verify"
              ? t("auth.verifyingEmail")
              : mode === "recovery"
              ? resetCodeSent
                ? t("auth.resettingPassword")
                : t("auth.sendingResetCode")
              : mode === "login"
              ? t("auth.loggingIn")
              : t("auth.signingUp")
            : mode === "verify"
              ? t("auth.verifyEmail")
              : mode === "recovery"
              ? resetCodeSent
                ? t("auth.resetPassword")
                : t("auth.sendResetCode")
              : mode === "login"
              ? t("auth.login")
              : t("auth.signup")}
        </button>

        <div className="auth__links">
          {mode === "verify" && (
            <button type="button" className="auth__link" onClick={onResendCode} disabled={busy}>
              {t("auth.resendCode")}
            </button>
          )}
          {mode !== "verify" && mode !== "recovery" && (
          <button type="button" className="auth__link" onClick={onForgot} disabled={busy}>
            {t("auth.forgot")}
          </button>
          )}

          {mode !== "verify" && mode !== "recovery" && (
          <button
            type="button"
            className="auth__link"
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setError(null);
              setMessage(null);
            }}
          >
            {mode === "login" ? t("auth.newAccount") : t("auth.haveAccount")}
          </button>
          )}

          {mode === "recovery" && (
            <button type="button" className="auth__link" onClick={returnToLogin} disabled={busy}>
              {t("auth.back")}
            </button>
          )}
        </div>
          </>
        )}
        </div>
      </div>
    </div>
  );
}
