import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  apiError,
  citation,
  json,
  memberMe,
  message,
  mockApi,
  page,
  renderAt,
  signedInAs,
} from "./utils";

const question = message({ role: "user", content: "How long do refunds take?" });
const answer = message({
  content: "Refunds are paid within 9 business days [S1].",
  citations: [citation],
  meta: { total_ms: 4200 },
});
const conversation = {
  id: "c1",
  title: "How long do refunds take?",
  created_at: "2026-11-01T10:05:00Z",
  updated_at: "2026-11-01T10:05:00Z",
};

async function ask(text: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Your question"), text);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  return user;
}

describe("chat", () => {
  it("shows an empty state before the first question", async () => {
    mockApi({ ...signedInAs(memberMe), "GET /api/v1/conversations": page([]) });
    renderAt("/chat");
    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask your documents" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("asks a question and renders the answer with its citations", async () => {
    let listed = false;
    const calls = mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/conversations": () => json(page(listed ? [conversation] : [])),
      "POST /api/v1/chat": () => {
        listed = true;
        return json({ conversation_id: "c1", question, answer });
      },
      "GET /api/v1/conversations/c1": { ...conversation, messages: [question, answer] },
    });
    renderAt("/chat");
    await ask("How long do refunds take?");

    const reply = await screen.findByTestId("answer");
    expect(reply).toHaveTextContent("Refunds are paid within 9 business days");
    expect(
      within(reply).getByRole("button", { name: "Open source S1: Refund policy" }),
    ).toBeInTheDocument();
    expect(within(reply).getByText("Refunds > Timing")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: conversation.title })).toBeInTheDocument();

    const sent = calls.find((c) => c.key === "POST /api/v1/chat");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({
      question: "How long do refunds take?",
      conversation_id: null,
    });
  });

  it("opens the cited passage in a drawer", async () => {
    const calls = mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/conversations": page([conversation]),
      "GET /api/v1/conversations/c1": { ...conversation, messages: [question, answer] },
      "GET /api/v1/documents/d1/chunks/k1": {
        id: "k1",
        document_id: "d1",
        ordinal: 2,
        content: "Approved refunds are paid within 9 business days of approval.",
        page_number: null,
        section: "Refunds > Timing",
      },
    });
    renderAt("/chat/c1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Open source S1/ }));

    const drawer = await screen.findByRole("dialog", { name: "Refund policy" });
    expect(await within(drawer).findByTestId("passage-text")).toHaveTextContent(
      "Approved refunds are paid within 9 business days of approval.",
    );
    expect(within(drawer).getByRole("link", { name: "Open document" })).toHaveAttribute(
      "href",
      "/documents/d1",
    );
    expect(calls.some((c) => c.key === "GET /api/v1/documents/d1/chunks/k1")).toBe(true);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("explains when the passage is no longer available", async () => {
    mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/conversations": page([conversation]),
      "GET /api/v1/conversations/c1": { ...conversation, messages: [question, answer] },
      "GET /api/v1/documents/d1/chunks/k1": () =>
        apiError(404, "not_found", "Passage not found"),
    });
    renderAt("/chat/c1");
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: /^S1\s*Refund policy/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passage not found");
  });

  it("marks answers the documents do not support", async () => {
    const abstained = message({
      content: "I couldn't find this in the documents you have access to.",
      abstained: true,
    });
    mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/conversations": page([]),
      "POST /api/v1/chat": { conversation_id: "c2", question, answer: abstained },
      "GET /api/v1/conversations/c2": { ...conversation, id: "c2", messages: [question, abstained] },
    });
    renderAt("/chat");
    await ask("What is the parking policy?");
    const reply = await screen.findByTestId("answer");
    expect(reply).toHaveTextContent("No supported answer");
    expect(within(reply).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the error and keeps the question when the model is unavailable", async () => {
    mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/conversations": page([]),
      "POST /api/v1/chat": () =>
        apiError(503, "model_unavailable", "The language model is not available right now"),
    });
    renderAt("/chat");
    await ask("How long do refunds take?");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The language model is not available right now",
    );
    expect(screen.getByLabelText("Your question")).toHaveValue("How long do refunds take?");
  });
});
