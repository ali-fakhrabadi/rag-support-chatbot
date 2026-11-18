import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api, ApiError, type Chunk, type Citation } from "../api";
import { unwrapLines } from "../text";

// Opens the exact stored passage a citation points to, fetched with the reader's own
// permissions (so a citation can never reveal text the reader may not see).
export function PassageDrawer({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [chunk, setChunk] = useState<Chunk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    setChunk(null);
    setError(null);
    api
      .chunk(citation.document_id, citation.chunk_id)
      .then(setChunk)
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "Could not load the passage"),
      );
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [citation, onClose]);

  const location = [
    citation.page !== null ? `Page ${citation.page}` : null,
    citation.section ? `Section: ${citation.section}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="passage-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <span className="source-tag">{citation.source_id}</span>
          <h2 id="passage-title">{citation.document_title}</h2>
          <button ref={closeRef} type="button" className="icon-button" onClick={onClose}>
            <span aria-hidden="true">×</span>
            <span className="sr-only">Close passage</span>
          </button>
        </header>
        {location && <p className="muted">{location}</p>}
        <p className="muted small">Similarity to your question: {citation.similarity.toFixed(2)}</p>
        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}
        {!chunk && !error && (
          <p role="status" className="muted">
            Loading passage…
          </p>
        )}
        {chunk && (
          <blockquote className="passage" data-testid="passage-text">
            {unwrapLines(chunk.content)}
          </blockquote>
        )}
        <Link className="button secondary" to={`/documents/${citation.document_id}`}>
          Open document
        </Link>
      </aside>
    </div>
  );
}
