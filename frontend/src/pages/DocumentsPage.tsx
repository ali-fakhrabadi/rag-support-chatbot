import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, ApiError, type AccessLevel, type DocumentItem } from "../api";
import { useSession } from "../App";
import { formatBytes, formatDate, StatusBadge } from "../components/StatusBadge";

const TYPE_LABEL: Record<string, string> = {
  "application/pdf": "PDF",
  "text/plain": "Text",
  "text/markdown": "Markdown",
};

export function DocumentsPage() {
  const { me } = useSession();
  const isAdmin = me?.role === "tenant_admin";
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [access, setAccess] = useState<AccessLevel>("all_members");
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .documents({ q: query || undefined })
      .then((page) => setDocuments(page.items))
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "Could not load documents"),
      );
  }, [query]);

  useEffect(load, [load]);

  // Poll while anything is still being indexed.
  const indexing = documents?.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!indexing) return;
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [indexing, load]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      await api.upload(file, access);
      setNotice(`"${file.name}" uploaded. Indexing has started.`);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function remove(doc: DocumentItem) {
    if (!window.confirm(`Delete "${doc.title}"? Its passages are removed from search at once.`)) {
      return;
    }
    try {
      await api.deleteDocument(doc.id);
      setNotice(`"${doc.title}" deleted.`);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed");
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Documents</h1>
          <p className="muted">
            The knowledge base for {me?.tenant.name}. Only documents marked "Ready" are searched.
          </p>
        </div>
      </header>

      {isAdmin && (
        <form className="card upload" onSubmit={upload} aria-label="Upload a document">
          <div className="upload-row">
            <label className="file-picker">
              <span>{file ? file.name : "Choose a PDF, TXT or Markdown file (max 20 MB)"}</span>
              <input
                ref={fileInput}
                type="file"
                accept=".pdf,.txt,.md,.markdown"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="inline">
              Visible to
              <select value={access} onChange={(e) => setAccess(e.target.value as AccessLevel)}>
                <option value="all_members">All members</option>
                <option value="admins_only">Admins only</option>
              </select>
            </label>
            <button type="submit" className="primary" disabled={!file || uploading}>
              {uploading ? "Uploading…" : "Upload"}
            </button>
          </div>
        </form>
      )}

      {notice && (
        <div className="alert success" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}

      <div className="toolbar">
        <label className="sr-only" htmlFor="doc-search">
          Filter by title
        </label>
        <input
          id="doc-search"
          type="search"
          placeholder="Filter by title"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {documents === null && !error && <p role="status">Loading documents…</p>}
      {documents?.length === 0 && (
        <div className="empty card">
          <h2>No documents yet</h2>
          <p className="muted">
            {isAdmin
              ? "Upload policies, manuals or guides to start asking questions."
              : "An admin of this workspace has not added any documents you can see yet."}
          </p>
        </div>
      )}
      {documents && documents.length > 0 && (
        <div className="table-wrap card">
          <table>
            <thead>
              <tr>
                <th scope="col">Title</th>
                <th scope="col">Type</th>
                <th scope="col">Status</th>
                <th scope="col">Passages</th>
                <th scope="col">Visible to</th>
                <th scope="col">Added</th>
                {isAdmin && <th scope="col"><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody>
              {documents.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link to={`/documents/${d.id}`}>{d.title}</Link>
                    <div className="muted small">
                      {d.filename} · {formatBytes(d.size_bytes)}
                    </div>
                  </td>
                  <td>{TYPE_LABEL[d.media_type] ?? d.media_type}</td>
                  <td>
                    <StatusBadge status={d.status} />
                    {d.error && <div className="error-text small">{d.error}</div>}
                  </td>
                  <td>{d.status === "ready" ? d.chunk_count : "—"}</td>
                  <td>{d.access_level === "admins_only" ? "Admins only" : "All members"}</td>
                  <td className="small">{formatDate(d.created_at)}</td>
                  {isAdmin && (
                    <td>
                      <button
                        type="button"
                        className="danger-link"
                        onClick={() => void remove(d)}
                        aria-label={`Delete ${d.title}`}
                      >
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
