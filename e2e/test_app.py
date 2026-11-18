"""End-to-end tests through the browser and the public API of a running deployment.

Tests marked `models` call the real embedding and language models and can take minutes each on
a CPU-only machine; run the others with `-m "not models"`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import ANSWER_TIMEOUT_MS, Api, ask, screenshot, sign_in
from playwright.sync_api import Page, expect

LARKSPUR_DOCUMENTS = 12
FENWICK_FILES = {"patient-billing-policy.md", "office-hours.txt", "staff-handbook.md"}

NewPage = Callable[..., Page]


# --- authentication and roles -------------------------------------------------------------------
def test_sign_in_rejects_a_wrong_password(new_page: NewPage) -> None:
    page = new_page()
    page.goto("/login")
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    screenshot(page, "01-sign-in")
    page.get_by_label("Work email").fill("agent@larkspur.example")
    page.get_by_label("Password").fill("not-the-right-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text("Invalid email, password or workspace")


def test_protected_pages_redirect_to_sign_in(new_page: NewPage) -> None:
    page = new_page()
    page.goto("/documents")
    expect(page).to_have_url(re.compile(r"/login$"))


def test_admin_sees_every_document_and_member_does_not_see_admin_only_ones(
    new_page: NewPage,
) -> None:
    admin = new_page()
    sign_in(admin, "larkspur_admin")
    admin.get_by_role("link", name="Documents").click()
    expect(admin.get_by_role("link", name="Compensation Bands 2026")).to_be_visible()
    expect(admin.get_by_role("row")).to_have_count(LARKSPUR_DOCUMENTS + 1)
    expect(admin.get_by_role("form", name="Upload a document")).to_be_visible()
    screenshot(admin, "02-documents-admin")

    member = new_page()
    sign_in(member, "larkspur_member")
    member.goto("/documents")
    expect(member.get_by_role("row")).to_have_count(LARKSPUR_DOCUMENTS)
    expect(member.get_by_text("Compensation Bands 2026")).to_have_count(0)
    expect(member.get_by_role("form", name="Upload a document")).to_have_count(0)
    expect(member.get_by_role("button", name=re.compile("^Delete"))).to_have_count(0)


def test_member_api_calls_for_admin_actions_are_refused() -> None:
    member = Api("larkspur_member")
    admin = Api("larkspur_admin")
    _, docs = admin.get("/api/v1/documents?limit=100")
    restricted = next(d for d in docs["items"] if d["access_level"] == "admins_only")
    assert member.get(f"/api/v1/documents/{restricted['id']}")[0] == 404
    assert member.get("/api/v1/tenants/current/members")[0] == 403
    assert member.get("/api/v1/tenants/current/audit-events")[0] == 403
    assert (
        Api.request("DELETE", f"/api/v1/documents/{restricted['id']}", token=member.token)[0] == 403
    )


# --- documents and ingestion --------------------------------------------------------------------
def test_document_details_show_ingestion_facts_and_passages(new_page: NewPage) -> None:
    page = new_page()
    sign_in(page, "larkspur_admin")
    page.goto("/documents")
    page.get_by_role("link", name="Larkspur Gear Warranty").click()
    expect(page.get_by_role("heading", name="Larkspur Gear Warranty")).to_be_visible()
    expect(page.get_by_text("3 pages", exact=False)).to_be_visible()
    expect(
        page.get_by_role("heading", name=re.compile(r"^Extracted passages \(\d+\)$"))
    ).to_be_visible()
    # The model name depends on the deployment (nomic-embed-text, or hashing-v1 in CI).
    model = page.locator("dt:text-is('Embedding model') + dd")
    expect(model).to_have_text(re.compile(r"\S+"))
    expect(model).not_to_have_text("—")
    screenshot(page, "03-document-detail")


def test_upload_is_indexed_then_deleted(new_page: NewPage, tmp_path: Path) -> None:
    """Runs in the separate demo workspace so it never changes the evaluation corpus."""
    page = new_page()
    sign_in(page, "demo_admin")
    page.goto("/documents")
    marker = uuid.uuid4().hex[:8]
    source = tmp_path / f"onboarding-{marker}.md"
    source.write_text(
        "# Onboarding checklist\n\n## First day\nNew starters receive their laptop and badge "
        f"at 9 a.m. on their first day. Reference {marker}.\n\n## First week\nEvery new starter "
        "meets their mentor within the first three working days.\n",
        encoding="utf-8",
    )
    page.get_by_label(re.compile("^Choose a PDF")).set_input_files(str(source))
    page.get_by_label("Visible to").select_option("admins_only")
    page.get_by_role("button", name="Upload").click()
    expect(page.get_by_role("status")).to_contain_text(f'"{source.name}" uploaded')

    row = page.get_by_role("row").filter(has_text=source.name)
    expect(row.get_by_text("Ready")).to_be_visible(timeout=180_000)
    expect(row.get_by_text("Admins only")).to_be_visible()

    page.once("dialog", lambda dialog: dialog.accept())
    row.get_by_role("button", name=re.compile("^Delete")).click()
    expect(page.get_by_role("status")).to_contain_text("deleted")
    expect(page.get_by_role("row").filter(has_text=source.name)).to_have_count(0)


def test_unsupported_files_are_rejected_with_a_clear_message(
    new_page: NewPage, tmp_path: Path
) -> None:
    page = new_page()
    sign_in(page, "demo_admin")
    page.goto("/documents")
    fake = tmp_path / "notes.md"
    fake.write_bytes(b"MZ\x90\x00 this is really an executable")
    page.get_by_label(re.compile("^Choose a PDF")).set_input_files(str(fake))
    page.get_by_role("button", name="Upload").click()
    expect(page.get_by_role("alert")).to_be_visible()


# --- tenant isolation (no models needed) ---------------------------------------------------------
def test_workspaces_cannot_see_each_others_documents(new_page: NewPage) -> None:
    larkspur, fenwick = Api("larkspur_admin"), Api("fenwick_admin")
    _, lark_docs = larkspur.get("/api/v1/documents?limit=100")
    _, fen_docs = fenwick.get("/api/v1/documents?limit=100")
    assert {d["filename"] for d in fen_docs["items"]} == FENWICK_FILES
    assert not {d["id"] for d in lark_docs["items"]} & {d["id"] for d in fen_docs["items"]}
    for doc in lark_docs["items"]:
        assert fenwick.get(f"/api/v1/documents/{doc['id']}")[0] == 404
        assert fenwick.get(f"/api/v1/documents/{doc['id']}/chunks")[0] == 404
    _, hits = fenwick.post(
        "/api/v1/search", {"query": "Larkspur Club points per dollar", "top_k": 5}
    )
    fenwick_ids = {d["id"] for d in fen_docs["items"]}
    assert all(h["document_id"] in fenwick_ids for h in hits["hits"])

    page = new_page()
    sign_in(page, "fenwick_admin")
    page.goto("/documents")
    expect(page.get_by_role("row")).to_have_count(len(FENWICK_FILES) + 1)
    expect(page.get_by_text("Fenwick Dental Group", exact=True)).to_be_visible()


def test_documents_page_on_a_phone(new_page: NewPage) -> None:
    page = new_page(390, 844)
    sign_in(page, "larkspur_member")
    page.goto("/documents")
    expect(page.get_by_role("link", name="Returns and Refunds Policy")).to_be_visible()
    screenshot(page, "09-mobile-documents")


# --- answers from the real models -----------------------------------------------------------------
@pytest.mark.models
def test_member_gets_a_cited_answer_and_opens_the_source(new_page: NewPage) -> None:
    page = new_page()
    sign_in(page, "larkspur_member")
    ask(page, "How many days do I have to return an item?")
    expect(page.get_by_role("status")).to_contain_text("Searching your documents")
    screenshot(page, "04-answer-in-progress")

    answer = page.get_by_test_id("answer").last
    expect(answer).to_contain_text("45", timeout=ANSWER_TIMEOUT_MS)
    sources = answer.get_by_role("list").get_by_role("button")
    expect(sources.first).to_contain_text("Returns and Refunds Policy")
    screenshot(page, "05-cited-answer")

    sources.first.click()
    drawer = page.get_by_role("dialog")
    expect(drawer.get_by_test_id("passage-text")).to_contain_text("45 days")
    screenshot(page, "06-source-passage")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(
        page.get_by_role("link", name="How many days do I have to return an item?")
    ).to_be_visible()


@pytest.mark.models
def test_question_outside_the_documents_is_declined(new_page: NewPage) -> None:
    page = new_page()
    sign_in(page, "larkspur_member")
    ask(page, "Does Larkspur price-match competitors?")
    answer = page.get_by_test_id("answer").last
    expect(answer).to_contain_text("No supported answer", timeout=ANSWER_TIMEOUT_MS)
    expect(answer.get_by_role("button")).to_have_count(0)
    screenshot(page, "07-no-supported-answer")


@pytest.mark.models
def test_member_cannot_get_admin_only_facts_through_chat(new_page: NewPage) -> None:
    page = new_page()
    sign_in(page, "larkspur_member")
    ask(page, "What is the salary band for a store manager?")
    answer = page.get_by_test_id("answer").last
    expect(answer).to_contain_text("No supported answer", timeout=ANSWER_TIMEOUT_MS)
    expect(answer).not_to_contain_text("68,000")


@pytest.mark.models
def test_second_workspace_answers_only_from_its_own_documents(new_page: NewPage) -> None:
    page = new_page()
    sign_in(page, "fenwick_admin")
    ask(page, "How many loyalty points do members earn per dollar?")
    first = page.get_by_test_id("answer").last
    expect(first).to_contain_text("No supported answer", timeout=ANSWER_TIMEOUT_MS)

    page.get_by_role("link", name="New question").click()
    ask(page, "What are the office hours on Friday?")
    answer = page.get_by_test_id("answer").last
    expect(answer).to_contain_text("1", timeout=ANSWER_TIMEOUT_MS)
    expect(answer.get_by_role("list").get_by_role("button").first).to_contain_text("Office Hours")
    screenshot(page, "08-second-workspace")
