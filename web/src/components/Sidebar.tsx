import { motion } from "motion/react";
import type { View } from "../types/ui";
import type { Quota } from "../types";
import { useI18n } from "../i18n";
import PlgLogo from "./PlgLogo";
import LangSwitcher from "./LangSwitcher";
import { IconAccount, IconHelp, IconHome, IconSettings } from "./icons";
import "./Sidebar.css";

// Order is the brief's, top to bottom: home, help, settings — then the
// profile pinned to the foot (Claude-style avatar-at-the-bottom). Settings
// moved up out of the foot so the three destinations read as one run
// and the foot holds only the account identity + its meters.
const NAV: { id: View; icon: typeof IconHome }[] = [
  { id: "home", icon: IconHome },
  { id: "help", icon: IconHelp },
  { id: "settings", icon: IconSettings },
];

// House spring (same as BeatComposer/Library/Tools entrances) — was a plain
// 120ms CSS ease; the tooltip reveal now uses real spring physics like the
// rest of the app's motion instead of a timing-curve approximation.
const SPRING = { type: "spring" as const, stiffness: 320, damping: 34, mass: 0.9 };
const TIP_VARIANTS = {
  rest: { opacity: 0, x: -4 },
  hover: { opacity: 1, x: 0 },
};

type Props = {
  view: View;
  onNavigate: (view: View) => void;
  quota?: Quota | null;
  authEmail?: string | null;
  /** The tester build has no account: the item is not shown. */
  hideAccount?: boolean;
};

export default function Sidebar({ view, onNavigate, authEmail, hideAccount }: Props) {
  const { t } = useI18n();

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <PlgLogo className="sidebar__logo" compact />
      </div>

      <nav className="sidebar__nav" aria-label="Main">
        {NAV.map(({ id, icon: Icon }) => (
          <motion.button
            key={id}
            type="button"
            className={`sidebar__item ${view === id ? "sidebar__item--active" : ""}`}
            onClick={() => onNavigate(id)}
            aria-current={view === id ? "page" : undefined}
            initial="rest"
            whileHover="hover"
            whileFocus="hover"
          >
            <Icon />
            <motion.span className="sidebar__tip" variants={TIP_VARIANTS} transition={SPRING}>
              {t(`nav.${id}`)}
            </motion.span>
          </motion.button>
        ))}
      </nav>

      <div className="sidebar__foot">
        <div className="sidebar__lang-wrap">
          <LangSwitcher />
          <span className="sidebar__tip sidebar__tip--lang">{t("settings.language")}</span>
        </div>

        {hideAccount ? null : (
        <motion.button
          type="button"
          className={`sidebar__item ${view === "account" ? "sidebar__item--active" : ""}`}
          onClick={() => onNavigate("account")}
          aria-current={view === "account" ? "page" : undefined}
          initial="rest"
          whileHover="hover"
          whileFocus="hover"
        >
          <IconAccount />
          <motion.span className="sidebar__tip" variants={TIP_VARIANTS} transition={SPRING}>
            {authEmail || t("nav.account")}
          </motion.span>
        </motion.button>
        )}
      </div>
    </aside>
  );
}
