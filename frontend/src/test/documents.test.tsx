import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  adminMe,
  apiError,
  json,
  makeDocument,
  memberMe,
  mockApi,
  page,
  renderAt,
  signedInAs,
} from "./utils";

const ready = makeDocument();
const failed = makeDocument({
  id: "d2",
  title: "Scanned contract",
  filename: "contract.pdf",
  media_type: "application/pdf",
  status: "failed",
  chunk_count: 0,
  error: "The PDF has no extractable text (scanned images are not supported)",
});

describe("documents", () => {
  it("lists documents with their ingestion status", async () => {
    mockApi({ ...signedInAs(memberMe), "GET /api/v1/documents": page([ready, failed]) });
    renderAt("/documents");

    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByText("Ready")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Markdown")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Failed")).toBeInTheDocument();
    expect(within(rows[2]!).getByText(/no extractable text/)).toBeInTheDocument();
  });

  it("hides upload and delete from members", async () => {
    mockApi({ ...signedInAs(memberMe), "GET /api/v1/documents": page([ready]) });
    renderAt("/documents");
    await screen.findByText("Refund policy");
    expect(screen.queryByRole("form", { name: "Upload a document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("shows a members-specific empty state", async () => {
    mockApi({ ...signedInAs(memberMe), "GET /api/v1/documents": page([]) });
    renderAt("/documents");
    expect(await screen.findByText(/has not added any documents/)).toBeInTheDocument();
  });

  it("uploads a file with the chosen access level", async () => {
    let uploaded = false;
    const processing = makeDocument({ id: "d3", title: "handbook", status: "processing" });
    const calls = mockApi({
      ...signedInAs(adminMe),
      "GET /api/v1/documents": () => json(page(uploaded ? [processing] : [])),
      "POST /api/v1/documents": () => {
        uploaded = true;
        return json({ document: processing, job: {} }, 202);
      },
    });
    renderAt("/documents");
    const user = userEvent.setup();
    const form = await screen.findByRole("form", { name: "Upload a document" });
    const file = new File(["# Handbook\n\nText"], "handbook.md", { type: "text/markdown" });
    await user.upload(within(form).getByLabelText(/Choose a PDF/), file);
    await user.selectOptions(within(form).getByLabelText("Visible to"), "admins_only");
    await user.click(within(form).getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("status")).toHaveTextContent('"handbook.md" uploaded');
    expect(await screen.findByText("Indexing")).toBeInTheDocument();
    const body = calls.find((c) => c.key === "POST /api/v1/documents")?.init?.body as FormData;
    expect(body.get("access_level")).toBe("admins_only");
    expect((body.get("file") as File).name).toBe("handbook.md");
  });

  it("shows why an upload was rejected", async () => {
    mockApi({
      ...signedInAs(adminMe),
      "GET /api/v1/documents": page([]),
      "POST /api/v1/documents": () =>
        apiError(415, "unsupported_media_type", "Only PDF, TXT and Markdown files are supported"),
    });
    renderAt("/documents");
    const user = userEvent.setup();
    const form = await screen.findByRole("form", { name: "Upload a document" });
    await user.upload(
      within(form).getByLabelText(/Choose a PDF/),
      new File(["x"], "notes.md", { type: "text/markdown" }),
    );
    await user.click(within(form).getByRole("button", { name: "Upload" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only PDF, TXT and Markdown");
  });

  it("deletes a document after confirmation", async () => {
    let deleted = false;
    const calls = mockApi({
      ...signedInAs(adminMe),
      "GET /api/v1/documents": () => json(page(deleted ? [] : [ready])),
      "DELETE /api/v1/documents/d1": () => {
        deleted = true;
        return new Response(null, { status: 204 });
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderAt("/documents");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Delete Refund policy" }));
    expect(await screen.findByText('"Refund policy" deleted.')).toBeInTheDocument();
    expect(calls.some((c) => c.key === "DELETE /api/v1/documents/d1")).toBe(true);
  });

  it("shows document details, ingestion facts and extracted passages", async () => {
    mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/documents/d1": ready,
      "GET /api/v1/documents/d1/jobs": [
        {
          id: "j1",
          document_id: "d1",
          status: "succeeded",
          stage: "done",
          progress: 100,
          attempts: 1,
          error: null,
          created_at: ready.created_at,
          started_at: ready.created_at,
          finished_at: ready.updated_at,
        },
      ],
      "GET /api/v1/documents/d1/chunks": {
        items: [
          {
            id: "k1",
            document_id: "d1",
            ordinal: 0,
            content: "Approved refunds are paid within 9 business days.",
            page_number: null,
            section: "Refunds > Timing",
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      },
    });
    renderAt("/documents/d1");
    expect(await screen.findByRole("heading", { name: "Refund policy" })).toBeInTheDocument();
    expect(await screen.findByText(/Approved refunds are paid/)).toBeInTheDocument();
    expect(screen.getByText("nomic-embed-text")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Extracted passages (1)" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restrict to admins/ })).not.toBeInTheDocument();
  });

  it("reports a document the user cannot see", async () => {
    mockApi({
      ...signedInAs(memberMe),
      "GET /api/v1/documents/d9": () => apiError(404, "not_found", "Document not found"),
      "GET /api/v1/documents/d9/jobs": () => apiError(404, "not_found", "Document not found"),
    });
    renderAt("/documents/d9");
    expect(await screen.findByRole("alert")).toHaveTextContent("Document not found");
  });
});
