import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router";
import { api, hasSession, setSignedOutHandler, type Me } from "./api";
import { Shell } from "./components/Shell";
import { ChatPage } from "./pages/ChatPage";
import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { LoginPage } from "./pages/LoginPage";

interface Session {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string, workspace: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(hasSession());
  const navigate = useNavigate();

  useEffect(() => {
    setSignedOutHandler(() => {
      setMe(null);
      navigate("/login");
    });
    if (hasSession()) {
      api
        .me()
        .then(setMe)
        .catch(() => setMe(null))
        .finally(() => setLoading(false));
    }
  }, [navigate]);

  const signIn = useCallback(async (email: string, password: string, workspace: string) => {
    await api.login(email, password, workspace);
    setMe(await api.me());
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setMe(null);
    navigate("/login");
  }, [navigate]);

  return (
    <SessionContext.Provider value={{ me, loading, signIn, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { me, loading } = useSession();
  if (loading) {
    return (
      <div className="center-screen" role="status">
        Loading…
      </div>
    );
  }
  return me ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <SessionProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/chat/:conversationId?"
          element={
            <Protected>
              <ChatPage />
            </Protected>
          }
        />
        <Route
          path="/documents"
          element={
            <Protected>
              <DocumentsPage />
            </Protected>
          }
        />
        <Route
          path="/documents/:documentId"
          element={
            <Protected>
              <DocumentDetailPage />
            </Protected>
          }
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </SessionProvider>
  );
}
