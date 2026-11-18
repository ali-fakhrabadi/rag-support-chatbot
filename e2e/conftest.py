"""Browser and API fixtures for end-to-end tests against a running deployment.

The tests use the evaluation workspaces created by evals/seed.sh (`larkspur`, `fenwick`) for
read-only checks and the `demo` workspace for anything that changes data, so they can run next
to an evaluation without changing its corpus.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://web:8080")
SCREENSHOTS = Path(os.environ.get("E2E_SCREENSHOTS", "/out/screenshots"))
ANSWER_TIMEOUT_MS = int(os.environ.get("E2E_ANSWER_TIMEOUT_S", "420")) * 1000


def _secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


ACCOUNTS = {
    "larkspur_admin": ("admin@larkspur.example", "larkspur", "eval_password"),
    "larkspur_member": ("agent@larkspur.example", "larkspur", "eval_password"),
    "fenwick_admin": ("admin@fenwick.example", "fenwick", "eval_password"),
    "demo_admin": ("admin@demo.example", "demo", "demo_password"),
}


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def new_page(browser: Browser) -> Iterator[Callable[..., Page]]:
    contexts = []

    def make(width: int = 1440, height: int = 900) -> Page:
        context = browser.new_context(
            base_url=BASE_URL,
            viewport={"width": width, "height": height},
            locale="en-US",
            timezone_id="UTC",
        )
        contexts.append(context)
        return context.new_page()

    yield make
    for context in contexts:
        context.close()


def sign_in(page: Page, account: str) -> None:
    """Sign in through the form. Waits out the login rate limit (all tests share one IP).

    Failures are reported with the page's alert text only: an assertion on the page would print
    an accessibility snapshot that includes the typed password.
    """
    email, workspace, secret = ACCOUNTS[account]
    for _ in range(5):
        page.goto("/login")
        page.get_by_label("Work email").fill(email)
        page.get_by_label("Password").fill(_secret(secret))
        page.get_by_label("Workspace").fill(workspace)
        page.get_by_role("button", name="Sign in").click()
        page.locator("h1:text-is('Ask your documents'), [role=alert]").first.wait_for(
            timeout=20_000
        )
        if page.get_by_role("heading", name="Ask your documents").count():
            return
        message = page.get_by_role("alert").inner_text()
        page.get_by_label("Password").fill("")
        if "Too many requests" not in message:
            raise AssertionError(f"sign-in as {account} failed: {message}")
        time.sleep(20)
    raise AssertionError(f"sign-in as {account} stayed rate limited")


def screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOTS / f"{name}.png"))


def ask(page: Page, question: str) -> None:
    page.get_by_label("Your question").fill(question)
    page.get_by_role("button", name="Ask").click()


class Api:
    """Direct API access with a given account, for checks the UI cannot express."""

    def __init__(self, account: str) -> None:
        email, workspace, secret = ACCOUNTS[account]
        body = {"email": email, "password": _secret(secret), "workspace": workspace}
        for _ in range(5):
            status, data = self.request("POST", "/api/v1/auth/login", body, token=None)
            if status != 429:
                break
            time.sleep(20)  # login rate limit: wait for the next window
        assert status == 200, data
        self.token = data["access_token"]

    @staticmethod
    def request(method: str, path: str, body: Any = None, *, token: str | None) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(  # noqa: S310 - test base URL
            BASE_URL + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as response:  # noqa: S310
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            return exc.code, json.loads(raw) if raw else None

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path, token=self.token)

    def post(self, path: str, body: Any) -> tuple[int, Any]:
        return self.request("POST", path, body, token=self.token)
