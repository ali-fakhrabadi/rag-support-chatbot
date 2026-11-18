import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { ApiError } from "../api";
import { useSession } from "../App";
import { Logo } from "../components/Shell";

export function LoginPage() {
  const { me, signIn } = useSession();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to="/chat" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password, workspace);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <section className="login-panel" aria-labelledby="login-title">
        <div className="login-brand">
          <Logo />
          <span>Secure RAG Knowledge Assistant</span>
        </div>
        <h1 id="login-title">Sign in</h1>
        <p className="muted">Answers from your team's documents, with sources you can check.</p>
        <form onSubmit={submit} noValidate>
          <label>
            Work email
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <label>
            Workspace <span className="muted">(only needed if you belong to several)</span>
            <input value={workspace} onChange={(e) => setWorkspace(e.target.value)} />
          </label>
          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          <button type="submit" className="primary" disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
      <aside className="login-aside" aria-hidden="true">
        <h2>Private by design</h2>
        <ul>
          <li>Answers cite the exact passage they come from</li>
          <li>Each workspace only ever sees its own documents</li>
          <li>Runs on your own infrastructure with a local model</li>
        </ul>
      </aside>
    </div>
  );
}
