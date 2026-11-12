/* Slim top strip — provider + language only. Nav lives in the sidebar. */

import LangSwitcher from "./LangSwitcher";
import "./AppHeader.css";

type Props = {
  provider?: string;
};

export default function AppHeader({ provider }: Props) {
  return (
    <header className="appbar">
      <div className="appbar__spacer" />
      <div className="appbar__right">
        {provider && <span className="appbar__provider">{provider}</span>}
        <LangSwitcher />
      </div>
    </header>
  );
}
