"""Tests for the site feedback form -> GitHub issue endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.feedback import FeedbackRequest, submit_feedback


def _request(**overrides):
    defaults = {
        "category": "bug",
        "message": "The bills page shows the wrong stage for some entries.",
        "page_url": "https://civitas-research.org/bills",
    }
    defaults.update(overrides)
    return FeedbackRequest(**defaults)


class TestValidation:
    def test_valid_request_accepted(self):
        req = _request()
        assert req.category == "bug"

    def test_invalid_category_rejected(self):
        with pytest.raises(ValidationError):
            _request(category="not-a-real-category")

    def test_too_short_message_rejected(self):
        with pytest.raises(ValidationError):
            _request(message="short")

    def test_whitespace_only_message_rejected(self):
        with pytest.raises(ValidationError):
            _request(message="          ")


class TestSubmitFeedback:
    @pytest.mark.asyncio
    async def test_returns_503_when_token_not_configured(self):
        with patch("app.api.feedback.settings") as mock_settings:
            mock_settings.FEEDBACK_TOKEN = ""
            with pytest.raises(HTTPException) as exc:
                await submit_feedback(_request(), None)
            assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_creates_github_issue_on_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/kamoras/civitas/issues/99"
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.api.feedback.settings") as mock_settings, \
             patch("app.api.feedback.make_async_client") as mock_client_cls:
            mock_settings.FEEDBACK_TOKEN = "fake-token"
            mock_settings.GITHUB_FEEDBACK_REPO = "kamoras/civitas"
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await submit_feedback(_request(), None)

        assert result.ok is True
        assert result.issue_url == "https://github.com/kamoras/civitas/issues/99"
        call_kwargs = mock_client.post.call_args
        assert "kamoras/civitas" in call_kwargs.args[0]
        assert "user-feedback" in call_kwargs.kwargs["json"]["labels"]

    @pytest.mark.asyncio
    async def test_github_error_response_surfaces_as_502(self):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation failed"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.api.feedback.settings") as mock_settings, \
             patch("app.api.feedback.make_async_client") as mock_client_cls:
            mock_settings.FEEDBACK_TOKEN = "fake-token"
            mock_settings.GITHUB_FEEDBACK_REPO = "kamoras/civitas"
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(HTTPException) as exc:
                await submit_feedback(_request(), None)
            assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_network_failure_surfaces_as_502(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))

        with patch("app.api.feedback.settings") as mock_settings, \
             patch("app.api.feedback.make_async_client") as mock_client_cls:
            mock_settings.FEEDBACK_TOKEN = "fake-token"
            mock_settings.GITHUB_FEEDBACK_REPO = "kamoras/civitas"
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(HTTPException) as exc:
                await submit_feedback(_request(), None)
            assert exc.value.status_code == 502

    def test_issue_body_includes_category_and_page(self):
        from app.api.feedback import _build_issue_body

        body = _build_issue_body(_request())
        assert "Bug report" in body
        assert "https://civitas-research.org/bills" in body

    def test_issue_body_never_carries_a_contact_field(self):
        """#95 (2026-07): a real email address went out in a public GitHub
        issue via the old optional "contact" field — the repo went public
        after that field shipped, and it kept publishing whatever a visitor
        typed there. There's no field left to carry one; pin that it stays
        gone rather than relying on absence-of-a-feature to hold on its
        own."""
        from app.api.feedback import _build_issue_body

        assert not hasattr(_request(), "email")
        assert "Contact" not in _build_issue_body(_request())


class TestFeedbackInjectionHardening:
    def test_message_is_fenced_so_mentions_and_links_are_inert(self):
        from app.api.feedback import _build_issue_body, FeedbackRequest
        body = FeedbackRequest(
            message="@torvalds see http://phish ![](http://track/x.png)",
            category="bug", page_url=None,
        )
        out = _build_issue_body(body)
        # The message is inside a fenced code block, so none of it renders.
        assert "```\n@torvalds see http://phish" in out

    def test_fence_extends_past_backticks_in_message(self):
        from app.api.feedback import _build_issue_body, FeedbackRequest
        body = FeedbackRequest(
            message="here is ``` a fence break attempt",
            category="bug", page_url=None,
        )
        out = _build_issue_body(body)
        # Opening fence must be longer than any backtick run in the body.
        assert out.startswith("````\n")

    def test_page_url_mentions_neutralized(self):
        from app.api.feedback import _build_issue_body, FeedbackRequest
        body = FeedbackRequest(
            message="a valid feedback message",
            category="bug", page_url="http://x/@evil#1",
        )
        out = _build_issue_body(body)
        assert "@evil" not in out  # zero-width space inserted after @/#
