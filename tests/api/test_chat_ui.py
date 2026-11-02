"""Offline checks for the gated, same-origin browser test interface."""

from html.parser import HTMLParser

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


@pytest.mark.parametrize("expose", [True, False])
async def test_chat_ui_follows_docs_setting(api_settings, expose):
    app = create_app(api_settings.model_copy(update={"expose_api_docs": expose}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path, media in (
            ("/chat", "text/html"),
            ("/chat/", "text/html"),
            ("/chat/app.js", "text/javascript"),
            ("/chat/style.css", "text/css"),
        ):
            response = await client.get(path)
            assert response.status_code == (200 if expose else 404)
            if expose:
                assert response.headers["content-type"].startswith(media)
                csp = response.headers["content-security-policy"]
                for directive in (
                    "default-src 'self'",
                    "script-src 'self'",
                    "style-src 'self'",
                    "connect-src 'self'",
                    "frame-ancestors 'none'",
                ):
                    assert directive in csp
                assert "unsafe-inline" not in csp
                assert response.headers["x-content-type-options"] == "nosniff"
        docs = await client.get("/docs")
        assert docs.status_code == (200 if expose else 404)
        assert "content-security-policy" not in docs.headers


async def test_chat_ui_absent_in_production():
    settings = Settings(
        _env_file=None,
        app_env="prod",
        llm_provider="openai",
        embedding_provider="openai",
        api_key_pepper=SecretStr("prod-pepper-test"),
        user_ref_key=SecretStr("prod-ref-test"),
        expose_api_docs=True,
    )
    assert not settings.expose_api_docs
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        for path in ("/chat", "/chat/", "/chat/app.js", "/chat/style.css"):
            assert (await client.get(path)).status_code == 404


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


async def test_chat_ui_static_security_and_api_contract(api_settings):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(api_settings)), base_url="http://testserver"
    ) as client:
        html = (await client.get("/chat")).text
        script = (await client.get("/chat/app.js")).text
        css = (await client.get("/chat/style.css")).text
        for content in (html, script, css):
            assert "http://" not in content
            assert "https://" not in content
            assert "innerHTML" not in content
            assert "localStorage" not in content
        assert 'fetch("/v1/chat/messages"' in script
        assert 'method: "POST"' in script
        assert '"X-API-Key": apiKey' in script
        assert "sessionStorage.setItem" in script
        assert "body.conversation_id = conversationId" in script
        assert 'addMessage(reply.welcome_message, "agent")' in script
        assert script.index('addMessage(reply.welcome_message, "agent")') < script.index(
            'addMessage(reply.reply, "agent")'
        )
        assert 'article.append(element("p", text, "message-text"))' in script
        assert "node.textContent = text" in script
        assert "white-space: pre-wrap" in css
        parser = PageParser()
        parser.feed(html)
        for tag, attrs in parser.tags:
            assert not any(name.startswith("on") or name == "style" for name in attrs)
            assert tag != "style"
            if tag == "script":
                assert attrs["src"] == "/chat/app.js"
            for name in ("href", "src"):
                if name in attrs:
                    assert attrs[name].startswith("/chat/")
        key = next(attrs for _, attrs in parser.tags if attrs.get("id") == "api-key")
        assert key["type"] == "password"
        assert key["autocomplete"] == "off"
