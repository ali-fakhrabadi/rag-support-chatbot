import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError, type Chunk, type DocumentItem, type Job } from "../api";
import { useSession } from "../App";
import { formatBytes, formatDate, StatusBadge } from "../components/StatusBadge";
import { unwrapLines } from "../text";

export function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const { me } = useSession();
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.document(documentId), api.documentJobs(documentId)])
      .then(([d, j]) => {
        setDoc(d);
        setJobs(j);
        if (d.status === "ready") {
          return api.documentChunks(documentId).then((page) => {
            setChunks(page.items);
            setTotal(page.total);
          });
        }
        return undefined;
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "Could not load the document"),
      );
  }, [documentId]);

  useEffect(load, [load]);

  const active = doc && (doc.status === "pending" || doc.status === "processing");
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, 1500);
    return () => window.clearInterval(timer);
  }, [active, load]);

  async function loadMore() {
    const page = await api.documentChunks(documentId, chunks.length);
    setChunks((current) => [...current, ...page.items]);
  }

  async function toggleAccess() {
    if (!doc) return;
    const next = doc.access_level === "all_members" ? "admins_only" : "all_members";
    setDoc(await api.setAccess(doc.id, next));
  }

  if (error) {
    return (
      <div className="page">
        <Link to="/documents">← Documents</Link>
        <div className="alert error" role="alert">
          {error}
        </div>
      </div>
    );
  }
  if (!doc) return <p role="status">Loading…</p>;
  const job = jobs[0];

  return (
    <div className="page">
      <Link to="/documents">← Documents</Link>
      <header className="page-header">
        <div>
          <h1>{doc.title}</h1>
          <p className="muted">
            {doc.filename} · {formatBytes(doc.size_bytes)}
            {doc.page_count !== null && ` · ${doc.page_count} pages`} · added{" "}
            {formatDate(doc.created_at)}
          </p>
        </div>
        <StatusBadge status={doc.status} />
      </header>

      <div className="grid-2">
        <section className="card" aria-labelledby="ingestion-title">
          <h2 id="ingestion-title">Ingestion</h2>
          {job ? (
            <>
              <div className="progress" aria-label="Indexing progress">
                <div style={{ width: `${job.progress}%` }} />
              </div>
              <dl className="facts">
                <dt>Status</dt>
                <dd>
                  <StatusBadge status={job.status} /> {job.status !== "succeeded" && job.stage}
                </dd>
                <dt>Attempts</dt>
                <dd>{job.attempts}</dd>
                <dt>Passages</dt>
                <dd>{doc.chunk_count}</dd>
                <dt>Embedding model</dt>
                <dd>{doc.embedding_model ?? "—"}</dd>
              </dl>
              {job.error && <div className="alert error">{job.error}</div>}
            </>
          ) : (
            <p className="muted">No ingestion job recorded.</p>
          )}
        </section>
        <section className="card" aria-labelledby="access-title">
          <h2 id="access-title">Access</h2>
          <p>
            Visible to{" "}
            <strong>{doc.access_level === "admins_only" ? "workspace admins only" : "all members"}</strong>
            .
          </p>
          {me?.role === "tenant_admin" && (
            <button type="button" className="secondary" onClick={() => void toggleAccess()}>
              {doc.access_level === "admins_only" ? "Share with all members" : "Restrict to admins"}
            </button>
          )}
        </section>
      </div>

      {doc.status === "ready" && (
        <section aria-labelledby="passages-title">
          <h2 id="passages-title">Extracted passages ({total})</h2>
          <ol className="passages">
            {chunks.map((c) => (
              <li key={c.id} className="card">
                <div className="muted small">
                  #{c.ordinal + 1}
                  {c.page_number !== null && ` · page ${c.page_number}`}
                  {c.section && ` · ${c.section}`}
                </div>
                <p>{unwrapLines(c.content)}</p>
              </li>
            ))}
          </ol>
          {chunks.length < total && (
            <button type="button" className="secondary" onClick={() => void loadMore()}>
              Show more
            </button>
          )}
        </section>
      )}
    </div>
  );
}
