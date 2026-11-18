import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, type Citation, type Conversation, type Message } from "../api";
import { PassageDrawer } from "../components/PassageDrawer";

// Turns "text [S1] more [S2]" into text with clickable citation buttons.
function AnswerText({
  text,
  citations,
  onOpen,
}: {
  text: string;
  citations: Citation[];
  onOpen: (c: Citation) => void;
}) {
  const parts = text.split(/(\[S\d+\])/g);
  return (
    <p>
      {parts.map((part, index) => {
        const match = /^\[(S\d+)\]$/.exec(part);
        const citation = match ? citations.find((c) => c.source_id === match[1]) : undefined;
        if (!match) return <span key={index}>{part}</span>;
        if (!citation) return null;
        return (
          <button
            key={index}
            type="button"
            className="cite-inline"
            onClick={() => onOpen(citation)}
            aria-label={`Open source ${citation.source_id}: ${citation.document_title}`}
          >
            {citation.source_id}
          </button>
        );
      })}
    </p>
  );
}

function AssistantMessage({ message, onOpen }: { message: Message; onOpen: (c: Citation) => void }) {
  if (message.abstained) {
    return (
      <div className="message assistant abstained" data-testid="answer">
        <div className="badge warn">No supported answer</div>
        <p>{message.content}</p>
      </div>
    );
  }
  return (
    <div className="message assistant" data-testid="answer">
      <AnswerText text={message.content} citations={message.citations} onOpen={onOpen} />
      {message.citations.length > 0 && (
        <div className="sources" aria-label="Sources">
          <span className="muted small">Sources</span>
          <ul>
            {message.citations.map((c) => (
              <li key={c.source_id}>
                <button type="button" className="source-chip" onClick={() => onOpen(c)}>
                  <span className="source-tag">{c.source_id}</span>
                  <span className="source-title">{c.document_title}</span>
                  {c.page !== null && <span className="muted small">p. {c.page}</span>}
                  {c.page === null && c.section && (
                    <span className="muted small">{c.section}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {message.meta?.total_ms !== undefined && (
        <div className="muted small">Answered in {(message.meta.total_ms / 1000).toFixed(1)} s</div>
      )}
    </div>
  );
}

export function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(() => {
    api
      .conversations()
      .then((page) => setConversations(page.items))
      .catch(() => undefined);
  }, []);

  useEffect(loadConversations, [loadConversations]);

  useEffect(() => {
    setError(null);
    if (!conversationId) {
      setMessages([]);
      return;
    }
    api
      .conversation(conversationId)
      .then((c) => setMessages(c.messages))
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "Could not load the conversation"),
      );
  }, [conversationId]);

  useEffect(() => {
    if (!pending) return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [pending]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, pending]);

  async function ask(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || pending) return;
    setPending(text);
    setQuestion("");
    setError(null);
    try {
      const reply = await api.ask(text, conversationId ?? null);
      setMessages((current) => [...current, reply.question, reply.answer]);
      if (!conversationId) navigate(`/chat/${reply.conversation_id}`, { replace: true });
      loadConversations();
    } catch (err) {
      setQuestion(text);
      setError(err instanceof ApiError ? err.message : "Something went wrong, try again");
    } finally {
      setPending(null);
    }
  }

  async function remove(id: string) {
    await api.deleteConversation(id).catch(() => undefined);
    if (id === conversationId) navigate("/chat");
    loadConversations();
  }

  return (
    <div className="chat-layout">
      <section className="history" aria-label="Conversation history">
        <Link to="/chat" className="button primary block">
          New question
        </Link>
        {conversations.length === 0 && <p className="muted small">No conversations yet.</p>}
        <ul>
          {conversations.map((c) => (
            <li key={c.id} className={c.id === conversationId ? "active" : ""}>
              <Link to={`/chat/${c.id}`} title={c.title}>
                {c.title}
              </Link>
              <button
                type="button"
                className="icon-button small"
                onClick={() => void remove(c.id)}
                aria-label={`Delete conversation ${c.title}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="conversation" aria-label="Conversation">
        <div className="messages" aria-live="polite">
          {messages.length === 0 && !pending && (
            <div className="empty">
              <h1>Ask your documents</h1>
              <p className="muted">
                Answers are written only from documents you can access, and every claim links to
                its source. If the documents don't cover it, the assistant says so.
              </p>
            </div>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="message user">
                <p>{m.content}</p>
              </div>
            ) : (
              <AssistantMessage key={m.id} message={m} onOpen={setCitation} />
            ),
          )}
          {pending && (
            <>
              <div className="message user">
                <p>{pending}</p>
              </div>
              <div className="message assistant thinking" role="status">
                <span className="dots" aria-hidden="true" />
                Searching your documents and writing an answer… {elapsed}s
              </div>
            </>
          )}
          <div ref={endRef} />
        </div>
        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}
        <form className="composer" onSubmit={ask}>
          <label htmlFor="question" className="sr-only">
            Your question
          </label>
          <textarea
            id="question"
            value={question}
            maxLength={2000}
            rows={2}
            placeholder="Ask a question about your documents…"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <button type="submit" className="primary" disabled={!question.trim() || pending !== null}>
            Ask
          </button>
        </form>
      </section>
      {citation && <PassageDrawer citation={citation} onClose={() => setCitation(null)} />}
    </div>
  );
}
