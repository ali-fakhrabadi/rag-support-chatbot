import type { DocumentStatus, JobStatus } from "../api";

const LABELS: Record<DocumentStatus | JobStatus, string> = {
  pending: "Queued",
  queued: "Queued",
  processing: "Indexing",
  running: "Indexing",
  ready: "Ready",
  succeeded: "Done",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: DocumentStatus | JobStatus }) {
  return <span className={`badge status-${status}`}>{LABELS[status]}</span>;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
