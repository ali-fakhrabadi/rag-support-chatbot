import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api, setSignedOutHandler } from "../api";
import { adminMe, apiError, json, mockApi, page, renderAt, signedInAs, tokens } from "./utils";

describe("login", () => {
  it("redirects to the login page without a session", async () => {
    mockApi({});
    renderAt("/documents");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows the server's error message for wrong credentials", async () => {
    mockApi({
      "POST /api/v1/auth/login": () => apiError(401, "invalid_credentials", "Invalid email or password"),
    });
    renderAt("/login");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Work email"), "admin@northwind.example");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(sessionStorage.getItem("rag.refresh")).toBeNull();
  });

  it("signs in, sends the workspace and lands on the chat page", async () => {
    const calls = mockApi({
      "POST /api/v1/auth/login": tokens,
      "GET /api/v1/me": adminMe,
      "GET /api/v1/conversations": page([]),
    });
    renderAt("/login");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Work email"), "admin@northwind.example");
    await user.type(screen.getByLabelText("Password"), "a-long-password");
    await user.type(screen.getByLabelText(/Workspace/), "northwind");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Ask your documents" })).toBeInTheDocument();
    const login = calls.find((c) => c.key === "POST /api/v1/auth/login");
    expect(JSON.parse(String(login?.init?.body))).toEqual({
      email: "admin@northwind.example",
      password: "a-long-password",
      workspace: "northwind",
    });
    expect(screen.getByText("Northwind Health")).toBeInTheDocument();
    expect(screen.getByText("Admin")).toBeInTheDocument();
  });

  it("signs out and forgets the refresh token", async () => {
    const calls = mockApi({
      ...signedInAs(adminMe),
      "GET /api/v1/conversations": page([]),
      "POST /api/v1/auth/logout": () => new Response(null, { status: 204 }),
    });
    renderAt("/chat");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/auth/logout")).toBe(true);
    expect(sessionStorage.getItem("rag.refresh")).toBeNull();
  });
});

describe("api client", () => {
  it("refreshes an expired access token once and retries the request", async () => {
    let meCalls = 0;
    let refreshes = 0;
    const calls = mockApi({
      "POST /api/v1/auth/refresh": () => {
        refreshes += 1;
        return json({ ...tokens, access_token: `access-${refreshes}` });
      },
      "GET /api/v1/me": () => {
        meCalls += 1;
        return meCalls === 1 ? apiError(401, "unauthorized", "expired") : json(adminMe);
      },
    });
    sessionStorage.setItem("rag.refresh", tokens.refresh_token);

    await expect(api.me()).resolves.toEqual(adminMe);
    expect(calls.map((c) => c.key)).toEqual([
      "POST /api/v1/auth/refresh",
      "GET /api/v1/me",
      "POST /api/v1/auth/refresh",
      "GET /api/v1/me",
    ]);
    const retried = new Headers(calls[3]?.init?.headers);
    expect(retried.get("Authorization")).toBe("Bearer access-2");
  });

  it("calls the signed-out handler when the refresh token is rejected", async () => {
    mockApi({ "POST /api/v1/auth/refresh": () => apiError(401, "invalid_token", "revoked") });
    sessionStorage.setItem("rag.refresh", tokens.refresh_token);
    const handler = vi.fn();
    setSignedOutHandler(handler);

    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalled();
    expect(sessionStorage.getItem("rag.refresh")).toBeNull();
  });

  it("restores the session after a reload", async () => {
    mockApi({ ...signedInAs(adminMe), "GET /api/v1/conversations": page([]) });
    renderAt("/chat");
    await waitFor(() => expect(screen.getByText("Avery Admin")).toBeInTheDocument());
  });
});
