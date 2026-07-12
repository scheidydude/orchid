"""Tests for orchid.servers.smtp — built-in SMTP MCP server."""

from __future__ import annotations

import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from orchid.servers.smtp import SMTPMCPServer, _addr_list, _build_message, main

# ── Helpers ───────────────────────────────────────────────────────────────────


SMTP_ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "587",
    "SMTP_USER": "sender@example.com",
    "SMTP_PASSWORD": "secret",
    "SMTP_USE_SSL": "false",
}


@pytest.fixture
def server():
    return SMTPMCPServer()


@pytest.fixture
def smtp_env(monkeypatch):
    """Inject SMTP env vars for all tests that need them."""
    for k, v in SMTP_ENV.items():
        monkeypatch.setenv(k, v)


# ── Unit: _addr_list ──────────────────────────────────────────────────────────


class TestAddrList:
    def test_single(self):
        assert _addr_list("a@b.com") == ["a@b.com"]

    def test_multiple(self):
        assert _addr_list("a@b.com, c@d.com,e@f.com") == [
            "a@b.com",
            "c@d.com",
            "e@f.com",
        ]

    def test_empty_string(self):
        assert _addr_list("") == []

    def test_strips_blanks(self):
        assert _addr_list("  x@y.com  ") == ["x@y.com"]


# ── Unit: _build_message ──────────────────────────────────────────────────────


class TestBuildMessage:
    def test_plain_text_returns_mimetext(self):
        msg = _build_message("from@x.com", "to@y.com", "Hi", "Body")
        assert isinstance(msg, MIMEText)
        assert msg["Subject"] == "Hi"
        assert msg["To"] == "to@y.com"
        assert msg["From"] == "from@x.com"
        assert msg.get_payload(decode=True).decode() == "Body"

    def test_html_returns_multipart(self):
        msg = _build_message("f@x.com", "t@y.com", "S", "plain", html_body="<b>hi</b>")
        assert isinstance(msg, MIMEMultipart)
        parts = msg.get_payload()
        assert parts[0].get_content_type() == "text/plain"
        assert parts[1].get_content_type() == "text/html"

    def test_cc_header_set(self):
        msg = _build_message("f@x.com", "t@y.com", "S", "B", cc="cc@y.com")
        assert msg["Cc"] == "cc@y.com"

    def test_no_cc_header_absent(self):
        msg = _build_message("f@x.com", "t@y.com", "S", "B")
        assert msg["Cc"] is None


# ── Unit: SMTPMCPServer.dispatch ─────────────────────────────────────────────


class TestDispatchInitialize:
    def test_returns_protocol_version(self, server):
        resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert "serverInfo" in resp["result"]
        assert resp["result"]["capabilities"] == {"tools": {}}

    def test_unknown_method_returns_error(self, server):
        resp = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "no_such_method", "params": {}})
        assert "error" in resp
        assert resp["error"]["code"] == -32601


class TestDispatchListTools:
    def test_returns_send_email_tool(self, server):
        resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tools = resp["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "send_email"
        schema = tools[0]["inputSchema"]
        assert "to" in schema["properties"]
        assert "subject" in schema["properties"]
        assert "body" in schema["properties"]
        assert schema["required"] == ["to", "subject", "body"]


class TestDispatchCallTool:
    def test_unknown_tool_returns_error(self, server, smtp_env):
        resp = server.dispatch({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        })
        assert resp["result"]["isError"] is True
        assert "Unknown tool" in resp["result"]["content"][0]["text"]


# ── Unit: send_email tool ─────────────────────────────────────────────────────


class TestSendEmail:
    def _call(self, server, args, env_override=None):
        base_env = dict(SMTP_ENV)
        if env_override:
            base_env.update(env_override)
        with patch.dict("os.environ", base_env, clear=False):
            return server._send_email(args)

    # --- Validation --------------------------------------------------

    def test_missing_to_returns_error(self, server):
        result = self._call(server, {"subject": "S", "body": "B"})
        assert result["isError"] is True
        assert "to" in result["content"][0]["text"]

    def test_missing_subject_returns_error(self, server):
        result = self._call(server, {"to": "a@b.com", "body": "B"})
        assert result["isError"] is True
        assert "subject" in result["content"][0]["text"]

    def test_missing_body_returns_error(self, server):
        result = self._call(server, {"to": "a@b.com", "subject": "S"})
        assert result["isError"] is True
        assert "body" in result["content"][0]["text"]

    def test_missing_smtp_user_returns_error(self, server):
        with patch.dict("os.environ", {"SMTP_USER": "", "SMTP_PASSWORD": "x"}, clear=False):
            result = server._send_email({"to": "a@b.com", "subject": "S", "body": "B"})
        assert result["isError"] is True
        assert "SMTP_USER" in result["content"][0]["text"]

    # --- STARTTLS success --------------------------------------------

    def test_starttls_send_success(self, server):
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp) as MockSMTP:
            mock_smtp.__enter__ = lambda s: s
            mock_smtp.__exit__ = MagicMock(return_value=False)
            result = self._call(server, {"to": "r@x.com", "subject": "Hi", "body": "Hello"})

        assert result["isError"] is False
        assert "r@x.com" in result["content"][0]["text"]
        mock_smtp.login.assert_called_once_with("sender@example.com", "secret")
        mock_smtp.sendmail.assert_called_once()
        args = mock_smtp.sendmail.call_args
        assert args[0][0] == "sender@example.com"
        assert args[0][1] == ["r@x.com"]

    # --- SSL success -------------------------------------------------

    def test_ssl_send_success(self, server):
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP_SSL", return_value=mock_smtp):
            mock_smtp.__enter__ = lambda s: s
            mock_smtp.__exit__ = MagicMock(return_value=False)
            result = self._call(
                server,
                {"to": "r@x.com", "subject": "Hi", "body": "Hello"},
                env_override={"SMTP_USE_SSL": "true", "SMTP_PORT": "465"},
            )
        assert result["isError"] is False

    # --- CC / BCC folded into recipients list ------------------------

    def test_cc_bcc_included_in_recipients(self, server):
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp):
            mock_smtp.__enter__ = lambda s: s
            mock_smtp.__exit__ = MagicMock(return_value=False)
            self._call(
                server,
                {
                    "to": "to@x.com",
                    "subject": "S",
                    "body": "B",
                    "cc": "cc@x.com",
                    "bcc": "bcc@x.com",
                },
            )
        recipients = mock_smtp.sendmail.call_args[0][1]
        assert "to@x.com" in recipients
        assert "cc@x.com" in recipients
        assert "bcc@x.com" in recipients

    # --- from_addr override ------------------------------------------

    def test_from_addr_override(self, server):
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp):
            mock_smtp.__enter__ = lambda s: s
            mock_smtp.__exit__ = MagicMock(return_value=False)
            self._call(
                server,
                {
                    "to": "r@x.com",
                    "subject": "S",
                    "body": "B",
                    "from_addr": "custom@override.com",
                },
            )
        assert mock_smtp.sendmail.call_args[0][0] == "custom@override.com"

    # --- SMTP_FROM env fallback --------------------------------------

    def test_smtp_from_env_used_when_no_from_addr(self, server):
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp):
            mock_smtp.__enter__ = lambda s: s
            mock_smtp.__exit__ = MagicMock(return_value=False)
            self._call(
                server,
                {"to": "r@x.com", "subject": "S", "body": "B"},
                env_override={"SMTP_FROM": "display@example.com"},
            )
        assert mock_smtp.sendmail.call_args[0][0] == "display@example.com"

    # --- Error handling ----------------------------------------------

    def test_auth_error_returns_error_result(self, server):
        with patch("smtplib.SMTP") as MockSMTP:
            MockSMTP.return_value.__enter__ = MagicMock(
                side_effect=smtplib.SMTPAuthenticationError(535, b"Bad credentials")
            )
            result = self._call(server, {"to": "r@x.com", "subject": "S", "body": "B"})
        assert result["isError"] is True
        assert "authentication" in result["content"][0]["text"].lower()

    def test_smtp_exception_returns_error_result(self, server):
        with patch("smtplib.SMTP") as MockSMTP:
            MockSMTP.return_value.__enter__ = MagicMock(
                side_effect=smtplib.SMTPException("connection refused")
            )
            result = self._call(server, {"to": "r@x.com", "subject": "S", "body": "B"})
        assert result["isError"] is True
        assert "SMTP error" in result["content"][0]["text"]

    def test_os_error_returns_error_result(self, server):
        with patch("smtplib.SMTP") as MockSMTP:
            MockSMTP.return_value.__enter__ = MagicMock(
                side_effect=OSError("unreachable")
            )
            result = self._call(server, {"to": "r@x.com", "subject": "S", "body": "B"})
        assert result["isError"] is True
        assert "Network error" in result["content"][0]["text"]


# ── Integration: main() I/O loop ─────────────────────────────────────────────


class TestMainLoop:
    """Test the stdin/stdout event loop in main()."""

    def _run_main(self, stdin_lines: list[str], env: dict | None = None) -> list[dict]:
        """Run main() with fake stdin, return parsed stdout lines."""
        import io

        stdin = io.StringIO("\n".join(stdin_lines) + "\n")
        stdout = io.StringIO()

        patch_env = patch.dict("os.environ", env or SMTP_ENV, clear=False)
        patch_stdin = patch("sys.stdin", stdin)
        patch_stdout = patch("sys.stdout", stdout)

        with patch_env, patch_stdin, patch_stdout:
            main()

        output = stdout.getvalue().strip()
        if not output:
            return []
        return [json.loads(line) for line in output.splitlines()]

    def test_initialize_roundtrip(self):
        msgs = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        ]
        responses = self._run_main(msgs)
        assert len(responses) == 1
        assert responses[0]["result"]["protocolVersion"] == "2024-11-05"

    def test_notification_produces_no_response(self):
        msgs = [
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        ]
        responses = self._run_main(msgs)
        # Only tools/list gets a response
        assert len(responses) == 1
        assert responses[0]["result"]["tools"][0]["name"] == "send_email"

    def test_invalid_json_skipped(self):
        msgs = [
            "not json at all",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        ]
        responses = self._run_main(msgs)
        assert len(responses) == 1

    def test_full_lifecycle(self):
        """initialize → notifications/initialized → tools/list → tools/call (mocked SMTP)."""
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = lambda s: s
        mock_smtp.__exit__ = MagicMock(return_value=False)

        msgs = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "send_email",
                    "arguments": {
                        "to": "dest@x.com",
                        "subject": "Digest",
                        "body": "Here are the repos.",
                    },
                },
            }),
        ]

        with patch("smtplib.SMTP", return_value=mock_smtp):
            responses = self._run_main(msgs)

        assert len(responses) == 3  # init + tools/list + tools/call (notification skipped)
        call_resp = responses[2]
        assert call_resp["id"] == 3
        assert call_resp["result"]["isError"] is False
        assert "dest@x.com" in call_resp["result"]["content"][0]["text"]
