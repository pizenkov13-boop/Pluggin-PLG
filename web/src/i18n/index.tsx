import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Locale, Messages, LocalePack, PromptCard } from "./types";
import { LOCALE_OPTIONS, RTL_LOCALES } from "./types";
import { en } from "./locales/en";
import { ru } from "./locales/ru";
import type { Quota } from "../types";

const STORAGE_KEY = "plg_locale";

const RAW: Record<Locale, Messages | LocalePack> = {
  en,
  ru,
};

const MESSAGES: Record<Locale, Messages> = Object.fromEntries(
  Object.entries(RAW).map(([locale, pack]) => [
    locale,
    {
      ...pack,
      nav: { ...en.nav, ...pack.nav },
      account: { ...en.account, ...(pack as { account?: Partial<Messages["account"]> }).account },
      auth: { ...en.auth, ...pack.auth },
      settings: { ...en.settings, ...pack.settings },
      tools: { ...en.tools, ...pack.tools },
      offline: { ...en.offline, ...pack.offline },
      errors: { ...en.errors, ...(pack as { errors?: Partial<Messages["errors"]> }).errors },
      status: {
        ...en.status,
        ...pack.status,
        phases: {
          ...en.status.phases,
          ...(pack.status as { phases?: Partial<Messages["status"]["phases"]> } | undefined)?.phases,
        },
      },
      session: { ...en.session, ...pack.session },
      regenerate: { ...en.regenerate, ...pack.regenerate },
      flOnboard: { ...en.flOnboard, ...pack.flOnboard },
      updates: { ...en.updates, ...pack.updates },
      help: {
        ...en.help,
        ...pack.help,
        docs: { ...en.help.docs, ...(pack.help?.docs ?? {}) },
      },
      prompts: { ...en.prompts, ...pack.prompts },
    },
  ]),
) as Record<Locale, Messages>;

function detectLocale(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY) as Locale | null;
  // PLG deliberately supports only Russian and English. A saved value from an
  // older build is normalized here instead of reviving an unsupported locale.
  if (saved === "ru" || saved === "en") return saved;

  const lang = (navigator.language || "en").toLowerCase();
  if (lang.startsWith("ru")) return "ru";
  return "en";
}

function getByPath(obj: unknown, path: string): string | undefined {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const part of parts) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === "string" ? cur : undefined;
}

export type TParams = Record<string, string | number>;

function interpolate(text: string, params?: TParams): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (_, key: string) => String(params[key] ?? ""));
}

type I18nContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: TParams) => string;
  messages: Messages;
  dir: "ltr" | "rtl";
  localeOptions: typeof LOCALE_OPTIONS;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => detectLocale());

  const setLocale = useCallback((next: Locale) => {
    const supportedLocale: Locale = next === "ru" ? "ru" : "en";
    setLocaleState(supportedLocale);
    localStorage.setItem(STORAGE_KEY, supportedLocale);
  }, []);

  const messages = MESSAGES[locale];
  const dir: "ltr" | "rtl" = RTL_LOCALES.includes(locale) ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = dir;
  }, [locale, dir]);

  const t = useCallback(
    (key: string, params?: TParams) => {
      const text = getByPath(messages, key) ?? getByPath(en, key) ?? key;
      return interpolate(text, params);
    },
    [messages],
  );

  const value = useMemo(
    () => ({ locale, setLocale, t, messages, dir, localeOptions: LOCALE_OPTIONS }),
    [locale, setLocale, t, messages, dir],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}

export function formatQuotaLabel(t: I18nContextValue["t"], quota: Quota): string {
  if (quota.skipped) return "";
  return t("quota.label", {
    remaining: quota.remaining,
    limit: quota.limit,
    days: quota.days_until_reset,
  });
}

const PROMPT_IDEA_ORDER = [
  "rageMelody",
  "darkTrap",
  "darkAtmospheric",
  "phonk",
  "evilPluck",
  "pluggnb",
  "detroit",
  "drill",
  "melodic",
  "ambient",
  "hyperpop",
  "westcoast",
] as const;

export type PromptIdea = { id: string } & PromptCard;

export function promptIdeas(messages: Messages): PromptIdea[] {
  const out: PromptIdea[] = [];
  for (const id of PROMPT_IDEA_ORDER) {
    const card = messages.prompts[id];
    if (card) out.push({ id, ...card });
  }
  return out;
}

/** @deprecated use promptIdeas */
export function promptCards(messages: Messages) {
  return promptIdeas(messages);
}
