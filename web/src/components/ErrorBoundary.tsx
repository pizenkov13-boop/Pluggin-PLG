import { Component } from "react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n";
import "./ErrorBoundary.css";

type Props = { children: ReactNode };
type State = { error: Error | null };

// getDerivedStateFromError/componentDidCatch only exist on class components,
// so the translated fallback UI lives in this small function component
// instead — useI18n() is a hook and can't be called from ErrorBoundary's
// own render().
function ErrorBoundaryFallback({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <div className="error-boundary" role="alert">
      <p className="error-boundary__title">{t("errors.somethingWentWrong")}</p>
      <p className="error-boundary__detail">{message}</p>
      <button type="button" className="error-boundary__retry" onClick={onRetry}>
        {t("errors.retry")}
      </button>
    </div>
  );
}

/* Contains a crash to the view that threw it instead of unmounting the whole
   shell (Sidebar included) — the failure mode we hit when LibraryView/
   ToolsView called the pywebview bridge before it was ready. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error("View crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <ErrorBoundaryFallback
          message={this.state.error.message}
          onRetry={() => this.setState({ error: null })}
        />
      );
    }
    return this.props.children;
  }
}
