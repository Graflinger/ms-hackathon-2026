import type { ReactNode } from "react";
import { AlertCircle, ArrowRight, LoaderCircle, RefreshCw } from "lucide-react";
import { ProjectLink as Link } from "./project";

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="lede">{description}</p>
      </div>
      {action && <div className="header-action">{action}</div>}
    </header>
  );
}
export function Status({ value }: { value?: string | null }) {
  const text = value || "not reported";
  const tone = [
    "pass",
    "passed",
    "approved",
    "accepted",
    "healthy",
    "ok",
    "completed",
    "complete",
    "fixed",
  ].includes(text)
    ? "good"
    : ["fail", "failed", "error", "rejected", "buggy"].includes(text)
      ? "bad"
      : [
            "pending",
            "running",
            "queued",
            "candidate",
            "unresolved",
            "incomplete",
            "interrupted",
            "blocked",
          ].includes(text)
        ? "warn"
        : "neutral";
  return (
    <span className={`status ${tone}`}>
      <span aria-hidden="true" />
      {text.replaceAll("_", " ")}
    </span>
  );
}
export function Loading({
  label = "Loading workbench data...",
}: {
  label?: string;
}) {
  return (
    <div className="state" role="status">
      <LoaderCircle className="spin" size={22} />
      <p>{label}</p>
    </div>
  );
}
export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={19} />
      <div>
        <strong>Something needs attention</strong>
        <p>{error instanceof Error ? error.message : String(error)}</p>
        {retry && (
          <button className="button small secondary" onClick={retry}>
            <RefreshCw size={14} />
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden="true">
        /
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Notice({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "info" | "success" | "warning";
}) {
  return (
    <div className={`notice ${tone}`} role="status">
      {children}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="json-view">
      {value == null ? "Not reported" : JSON.stringify(value, null, 2)}
    </pre>
  );
}
export function SectionHeading({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        <h2>{title}</h2>
        {detail && <p>{detail}</p>}
      </div>
      {action}
    </div>
  );
}
export function TextLink({
  to,
  children,
}: {
  to: string;
  children: ReactNode;
}) {
  return (
    <Link className="text-link" to={to}>
      {children}
      <ArrowRight size={15} />
    </Link>
  );
}
export function DateLabel({ value }: { value?: string }) {
  if (!value) return <>Not reported</>;
  const date = new Date(value);
  return (
    <time dateTime={value} title={value}>
      {Number.isNaN(date.valueOf())
        ? value
        : date.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
    </time>
  );
}
export function ShortId({ value }: { value: string }) {
  return <code title={value}>{value.slice(0, 8)}</code>;
}
