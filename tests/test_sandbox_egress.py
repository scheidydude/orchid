import shutil
from unittest.mock import MagicMock, patch

import pytest

from orchid import sandbox_egress


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Redirect the module's state dir into a tmp_path for every test."""
    monkeypatch.setattr(sandbox_egress, "_STATE_DIR", tmp_path / "sandbox_egress")


def test_write_allowlist_prefixes_domains_with_dot(tmp_path) -> None:
    path = sandbox_egress._write_allowlist(["example.com", ".already-dotted.com"])
    content = path.read_text()
    assert ".example.com" in content
    assert ".already-dotted.com" in content
    assert "..already-dotted.com" not in content


def test_write_allowlist_empty_list_writes_empty_file() -> None:
    path = sandbox_egress._write_allowlist([])
    assert path.read_text() == ""


def test_ensure_conf_copies_template(tmp_path) -> None:
    conf = sandbox_egress._ensure_conf()
    assert conf.exists()
    assert "http_port 3128" in conf.read_text()
    assert "allowed_dst_domains" in conf.read_text()


def test_ensure_egress_proxy_raises_when_docker_unavailable() -> None:
    with patch.object(shutil, "which", return_value=None):
        with pytest.raises(sandbox_egress.EgressProxyError, match="docker is not available"):
            sandbox_egress.ensure_egress_proxy(["example.com"])


def test_ensure_egress_proxy_starts_container_when_not_running() -> None:
    """Fresh state: network doesn't exist, container isn't running -> create both."""
    fake_ip = "172.99.0.5"

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.stdout = ""
        result.stderr = ""
        if cmd[:3] == ["docker", "network", "inspect"]:
            result.returncode = 1  # network doesn't exist yet
        elif cmd[:3] == ["docker", "network", "create"]:
            result.returncode = 0
        elif "-f" in cmd and any("IPAddress" in part for part in cmd):
            # the IP lookup, distinguished from the plain running/exists checks below
            result.returncode = 0
            result.stdout = fake_ip
        elif cmd[:2] == ["docker", "inspect"]:
            result.returncode = 1  # container doesn't exist / isn't running yet
        elif cmd[:3] == ["docker", "run", "-d"]:
            result.returncode = 0
        elif cmd[:3] == ["docker", "network", "connect"]:
            result.returncode = 0
        else:
            result.returncode = 0
        return result

    with patch.object(shutil, "which", return_value="/usr/bin/docker"), \
         patch("orchid.sandbox_egress._run", side_effect=fake_run) as mock_run:
        proxy_url = sandbox_egress.ensure_egress_proxy(["example.com"])

    assert proxy_url == f"http://{fake_ip}:{sandbox_egress.PROXY_PORT}"
    called_cmds = [call.args[0] for call in mock_run.call_args_list]
    assert ["docker", "network", "create", "--internal", sandbox_egress.INTERNAL_NETWORK] in called_cmds
    assert any(cmd[:3] == ["docker", "run", "-d"] for cmd in called_cmds)
    assert ["docker", "network", "connect", sandbox_egress.INTERNAL_NETWORK, sandbox_egress.PROXY_CONTAINER] in called_cmds
