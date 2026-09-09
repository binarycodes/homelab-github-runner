import base64
import io
import json
import os
import signal
import sys
import threading
import urllib.error
import urllib.request

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import entrypoint

ENV = {
    "GH_RUNNER_URL": "https://github.com/acme",
    "GH_APP_ID": "12345",
    "GH_APP_INSTALLATION_ID": "67890",
    "GH_APP_PRIVATE_KEY_FILE": "/run/secrets/app.pem",
}


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TestSettings:
    def test_reads_every_variable(self):
        env = {**ENV, "GH_RUNNER_LABELS": "self-hosted,arm64", "GH_RUNNER_NAME": "node-1"}
        settings = entrypoint.Settings.from_env(env)
        assert settings.runner_url == "https://github.com/acme"
        assert settings.app_id == "12345"
        assert settings.installation_id == "67890"
        assert str(settings.private_key_file) == "/run/secrets/app.pem"
        assert settings.labels == "self-hosted,arm64"
        assert settings.name == "node-1"

    def test_defaults_labels_and_name(self):
        settings = entrypoint.Settings.from_env(ENV)
        assert settings.labels == "self-hosted"
        assert settings.name.startswith("ephemeral-")

    def test_empty_optional_variable_falls_back_to_default(self):
        settings = entrypoint.Settings.from_env({**ENV, "GH_RUNNER_LABELS": ""})
        assert settings.labels == "self-hosted"

    def test_reports_every_missing_variable_at_once(self):
        with pytest.raises(entrypoint.ConfigError) as info:
            entrypoint.Settings.from_env({"GH_APP_ID": "1", "GH_APP_INSTALLATION_ID": ""})
        assert str(info.value) == (
            "missing environment: GH_RUNNER_URL GH_APP_INSTALLATION_ID GH_APP_PRIVATE_KEY_FILE"
        )


class TestCreateJwt:
    def test_claims_and_signature(self, private_key):
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        token = entrypoint.create_jwt("12345", pem, now=1_700_000_000)

        header, claims, signature = token.split(".")
        assert json.loads(b64url_decode(header)) == {"alg": "RS256", "typ": "JWT"}
        assert json.loads(b64url_decode(claims)) == {
            "iat": 1_700_000_000 - 60,
            "exp": 1_700_000_000 + 540,
            "iss": "12345",
        }
        private_key.public_key().verify(
            b64url_decode(signature),
            f"{header}.{claims}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_uses_base64url_without_padding(self, private_key):
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        token = entrypoint.create_jwt("1", pem)
        assert "=" not in token
        assert "+" not in token
        assert "/" not in token


class TestRunnerTokenUrl:
    @pytest.mark.parametrize(
        ("runner_url", "expected"),
        [
            ("https://github.com/acme", "orgs/acme"),
            ("https://github.com/acme/", "orgs/acme"),
            ("https://github.com/acme/widgets", "repos/acme/widgets"),
            ("https://github.com/acme/widgets/", "repos/acme/widgets"),
            # a repository named after its owner is still a repository
            ("https://github.com/acme/acme", "repos/acme/acme"),
        ],
    )
    def test_maps_org_and_repo_urls(self, runner_url, expected):
        assert entrypoint.runner_token_url(runner_url, "registration-token") == (
            f"https://api.github.com/{expected}/actions/runners/registration-token"
        )

    def test_remove_token_kind(self):
        url = entrypoint.runner_token_url("https://github.com/acme", "remove-token")
        assert url == "https://api.github.com/orgs/acme/actions/runners/remove-token"

    @pytest.mark.parametrize(
        ("runner_url", "message"),
        [
            ("https://github.com/enterprises/acme", "enterprise"),
            ("https://github.com/", "organization or a repository"),
            ("https://github.com/acme/widgets/settings", "organization or a repository"),
            ("http://github.com/acme", "https://github.com/"),
            ("https://ghe.example.com/acme", "https://github.com/"),
            ("acme/widgets", "https://github.com/"),
        ],
    )
    def test_rejects_unsupported_urls(self, runner_url, message):
        with pytest.raises(entrypoint.ConfigError, match=message):
            entrypoint.runner_token_url(runner_url, "registration-token")


class TestPostJson:
    def test_sends_bearer_token_and_parses_the_body(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return io.BytesIO(b'{"token": "ghs_abc"}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert entrypoint.post_json("https://api.github.com/x", "jwt-1") == {"token": "ghs_abc"}
        request = seen["request"]
        assert request.get_method() == "POST"
        assert request.full_url == "https://api.github.com/x"
        assert request.get_header("Authorization") == "Bearer jwt-1"
        assert request.get_header("Accept") == "application/vnd.github+json"
        assert seen["timeout"] > 0

    def test_http_error_includes_status_and_body(self, monkeypatch):
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}')
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(entrypoint.ApiError, match=r'HTTP 404: \{"message":"Not Found"\}'):
            entrypoint.post_json("https://api.github.com/x", "jwt-1")

    def test_connection_error_is_reported(self, monkeypatch):
        def fake_urlopen(request, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(entrypoint.ApiError, match="connection refused"):
            entrypoint.post_json("https://api.github.com/x", "jwt-1")


class TestRunnerAdminToken:
    def test_chains_jwt_installation_and_runner_tokens(self, monkeypatch, tmp_path, private_key):
        key_file = tmp_path / "app.pem"
        key_file.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        settings = entrypoint.Settings.from_env(
            {
                **ENV,
                "GH_RUNNER_URL": "https://github.com/acme/acme",
                "GH_APP_PRIVATE_KEY_FILE": str(key_file),
            }
        )
        calls = []

        def fake_post_json(url, bearer):
            calls.append((url, bearer))
            return {"token": f"token-{len(calls)}"}

        monkeypatch.setattr(entrypoint, "post_json", fake_post_json)
        assert entrypoint.runner_admin_token(settings, "remove-token") == "token-2"

        (installation_url, jwt), (runner_url, installation_token) = calls
        assert installation_url == "https://api.github.com/app/installations/67890/access_tokens"
        assert jwt.count(".") == 2
        assert runner_url == "https://api.github.com/repos/acme/acme/actions/runners/remove-token"
        assert installation_token == "token-1"


class TestRunListener:
    def test_returns_the_exit_status_and_sets_the_trap_variable(self):
        script = 'test "$RUNNER_MANUALLY_TRAP_SIG" = 1 || exit 99; exit 7'
        assert entrypoint.run_listener(["sh", "-c", script]) == 7

    def test_relays_sigterm_and_restores_the_handlers(self, tmp_path):
        ready = tmp_path / "ready"
        script = f"trap 'exit 143' TERM; : > {ready}; while :; do sleep 0.1; done"

        def stop_once_ready():
            while not ready.exists():
                pass
            os.kill(os.getpid(), signal.SIGTERM)

        before = signal.getsignal(signal.SIGTERM)
        threading.Thread(target=stop_once_ready, daemon=True).start()
        assert entrypoint.run_listener(["sh", "-c", script]) == 143
        assert signal.getsignal(signal.SIGTERM) is before


class TestMain:
    def test_missing_configuration_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(os, "environ", {})
        monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None)
        assert entrypoint.main() == 2
        assert "GH_RUNNER_URL" in capsys.readouterr().err
