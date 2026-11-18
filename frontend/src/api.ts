// Typed client for the backend API. The access token lives in memory only; the refresh token
// is kept in sessionStorage so a page reload in the same tab stays signed in. Both are cleared
// on logout. See docs/security.md for the trade-offs.

export type Role = "tenant_admin" | "member";
export type AccessLevel = "all_members" | "admins_only";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Me {
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  tenant: { id: string; slug: string; name: string };
}

export interface DocumentItem {
  id: string;
  title: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  access_level: AccessLevel;
  status: DocumentStatus;
  page_count: number | null;
  chunk_count: number;
  embedding_model: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  document_id: string;
  status: JobStatus;
  stage: string;
  progress: number;
  attempts: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Chunk {
  id: string;
  document_id: string;
  ordinal: number;
  content: string;
  page_number: number | null;
  section: string | null;
}

export interface Citation {
  source_id: string;
  document_id: string;
  document_title: string;
  chunk_id: string;
  page: number | null;
  section: string | null;
  snippet: string;
  similarity: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  abstained: boolean;
  created_at: string;
  meta?: { total_ms?: number; abstain_reason?: string | null; model?: string | null } | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const REFRESH_KEY = "rag.refresh";
let accessToken: string | null = null;
let onSignedOut: (() => void) | null = null;

export function setSignedOutHandler(handler: () => void): void {
  onSignedOut = handler;
}

export function hasSession(): boolean {
  return accessToken !== null || sessionStorage.getItem(REFRESH_KEY) !== null;
}

function storeTokens(access: string, refresh: string): void {
  accessToken = access;
  sessionStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  accessToken = null;
  sessionStorage.removeItem(REFRESH_KEY);
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } };
    return new ApiError(
      response.status,
      body.error?.code ?? "error",
      body.error?.message ?? response.statusText,
    );
  } catch {
    return new ApiError(response.status, "error", response.statusText || "Request failed");
  }
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const token = sessionStorage.getItem(REFRESH_KEY);
  if (!token) return false;
  refreshing ??= (async () => {
    const response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
    if (!response.ok) {
      clearTokens();
      return false;
    }
    const body = (await response.json()) as { access_token: string; refresh_token: string };
    storeTokens(body.access_token, body.refresh_token);
    return true;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  if (accessToken === null && !(await refresh())) {
    onSignedOut?.();
    throw new ApiError(401, "unauthorized", "Please sign in");
  }
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && retry && (await refresh())) {
    return request<T>(path, init, false);
  }
  if (response.status === 401) {
    clearTokens();
    onSignedOut?.();
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  async login(email: string, password: string, workspace: string): Promise<void> {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, workspace: workspace || null }),
    });
    if (!response.ok) throw await parseError(response);
    const body = (await response.json()) as { access_token: string; refresh_token: string };
    storeTokens(body.access_token, body.refresh_token);
  },
  async logout(): Promise<void> {
    const token = sessionStorage.getItem(REFRESH_KEY);
    try {
      if (token) {
        await request("/api/v1/auth/logout", {
          method: "POST",
          body: JSON.stringify({ refresh_token: token }),
        });
      }
    } finally {
      clearTokens();
    }
  },
  me: () => request<Me>("/api/v1/me"),
  documents: (params: { limit?: number; offset?: number; q?: string } = {}) => {
    const query = new URLSearchParams({
      limit: String(params.limit ?? 50),
      offset: String(params.offset ?? 0),
    });
    if (params.q) query.set("q", params.q);
    return request<Page<DocumentItem>>(`/api/v1/documents?${query}`);
  },
  document: (id: string) => request<DocumentItem>(`/api/v1/documents/${id}`),
  documentJobs: (id: string) => request<Job[]>(`/api/v1/documents/${id}/jobs`),
  documentChunks: (id: string, offset = 0) =>
    request<Page<Chunk>>(`/api/v1/documents/${id}/chunks?limit=20&offset=${offset}`),
  chunk: (documentId: string, chunkId: string) =>
    request<Chunk>(`/api/v1/documents/${documentId}/chunks/${chunkId}`),
  upload: (file: File, accessLevel: AccessLevel) => {
    const form = new FormData();
    form.append("file", file);
    form.append("access_level", accessLevel);
    return request<{ document: DocumentItem; job: Job }>("/api/v1/documents", {
      method: "POST",
      body: form,
    });
  },
  setAccess: (id: string, access_level: AccessLevel) =>
    request<DocumentItem>(`/api/v1/documents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ access_level }),
    }),
  deleteDocument: (id: string) => request<void>(`/api/v1/documents/${id}`, { method: "DELETE" }),
  conversations: () => request<Page<Conversation>>("/api/v1/conversations?limit=50"),
  conversation: (id: string) =>
    request<Conversation & { messages: Message[] }>(`/api/v1/conversations/${id}`),
  deleteConversation: (id: string) =>
    request<void>(`/api/v1/conversations/${id}`, { method: "DELETE" }),
  ask: (question: string, conversationId: string | null) =>
    request<{ conversation_id: string; question: Message; answer: Message }>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({ question, conversation_id: conversationId }),
    }),
};
