import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import type { Citation, DocumentItem, Me, Message } from "../api";
import { App } from "../App";

export interface Call {
  key: string;
  init: RequestInit | undefined;
  url: URL;
}

type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function apiError(status: number, code: string, message: string): Response {
  return json({ error: { code, message, request_id: "test" } }, status);
}

export function page<T>(items: T[]) {
  return { items, total: items.length, limit: 50, offset: 0 };
}

// Replaces fetch with a table of "METHOD /path" -> handler or JSON body. Unknown routes fail
// loudly so a test never silently depends on an endpoint it did not declare.
export function mockApi(routes: Record<string, Handler | object>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    calls.push({ key, init, url });
    const route = routes[key];
    if (route === undefined) return apiError(404, "not_found", `No mock for ${key}`);
    return typeof route === "function" ? (route as Handler)(init, url) : json(route);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export const tokens = { access_token: "access", refresh_token: "r".repeat(43), expires_in: 900 };

export const adminMe: Me = {
  user_id: "u-admin",
  email: "admin@northwind.example",
  full_name: "Avery Admin",
  role: "tenant_admin",
  tenant: { id: "t1", slug: "northwind", name: "Northwind Health" },
};

export const memberMe: Me = { ...adminMe, user_id: "u-member", role: "member", full_name: "Max Member" };

// Routes that restore a signed-in session from the stored refresh token.
export function signedInAs(me: Me) {
  sessionStorage.setItem("rag.refresh", tokens.refresh_token);
  return { "POST /api/v1/auth/refresh": tokens, "GET /api/v1/me": me };
}

export function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

export function makeDocument(overrides: Partial<DocumentItem> = {}): DocumentItem {
  return {
    id: "d1",
    title: "Refund policy",
    filename: "refund-policy.md",
    media_type: "text/markdown",
    size_bytes: 2048,
    access_level: "all_members",
    status: "ready",
    page_count: null,
    chunk_count: 4,
    embedding_model: "nomic-embed-text",
    error: null,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-01T10:01:00Z",
    ...overrides,
  };
}

export const citation: Citation = {
  source_id: "S1",
  document_id: "d1",
  document_title: "Refund policy",
  chunk_id: "k1",
  page: null,
  section: "Refunds > Timing",
  snippet: "Refunds are paid within 9 business days.",
  similarity: 0.82,
};

export function message(overrides: Partial<Message>): Message {
  return {
    id: crypto.randomUUID(),
    role: "assistant",
    content: "",
    citations: [],
    abstained: false,
    created_at: "2026-11-01T10:05:00Z",
    meta: null,
    ...overrides,
  };
}
