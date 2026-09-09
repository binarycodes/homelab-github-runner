#!/usr/bin/env python3
"""Register an ephemeral GitHub Actions runner through a GitHub App and run it."""

from __future__ import annotations

import base64
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

API = "https://api.github.com"
REQUIRED_ENV = ("GH_RUNNER_URL", "GH_APP_ID", "GH_APP_INSTALLATION_ID", "GH_APP_PRIVATE_KEY_FILE")


class ConfigError(Exception):
    pass


class ApiError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    runner_url: str
    app_id: str
    installation_id: str
    private_key_file: Path
    labels: str
    name: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        missing = [key for key in REQUIRED_ENV if not env.get(key)]
        if missing:
            raise ConfigError(f"missing environment: {' '.join(missing)}")
        return cls(
            runner_url=env["GH_RUNNER_URL"],
            app_id=env["GH_APP_ID"],
            installation_id=env["GH_APP_INSTALLATION_ID"],
            private_key_file=Path(env["GH_APP_PRIVATE_KEY_FILE"]),
            labels=env.get("GH_RUNNER_LABELS") or "self-hosted",
            name=env.get("GH_RUNNER_NAME") or f"ephemeral-{socket.gethostname()}-{random.randrange(32768)}",
        )


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def create_jwt(app_id: str, private_key_pem: bytes, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iat": now - 60, "exp": now + 540, "iss": app_id}
    unsigned = ".".join(b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims))
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{unsigned}.{b64url(signature)}"


def runner_api_prefix(runner_url: str) -> str:
    """The API path under which the runner URL's registration endpoints live."""
    parsed = urlparse(runner_url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ConfigError(f"GH_RUNNER_URL must start with https://github.com/, got {runner_url!r}")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] == "enterprises":
        raise ConfigError(
            "GH_RUNNER_URL points at an enterprise; a GitHub App installation cannot register enterprise runners"
        )
    if len(parts) == 1:
        return f"orgs/{parts[0]}"
    if len(parts) == 2:
        return f"repos/{parts[0]}/{parts[1]}"
    raise ConfigError(f"GH_RUNNER_URL must name an organization or a repository, got {runner_url!r}")


def runner_token_url(runner_url: str, kind: str) -> str:
    return f"{API}/{runner_api_prefix(runner_url)}/actions/runners/{kind}"


def post_json(url: str, bearer: str) -> dict:
    request = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace").strip()
        raise ApiError(f"POST {url} returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise ApiError(f"POST {url} failed: {error.reason}") from error


def runner_admin_token(settings: Settings, kind: str) -> str:
    """Fetch a runner registration-token or remove-token, authenticating from scratch."""
    jwt = create_jwt(settings.app_id, settings.private_key_file.read_bytes())
    installation_url = f"{API}/app/installations/{settings.installation_id}/access_tokens"
    installation_token = post_json(installation_url, jwt)["token"]
    return post_json(runner_token_url(settings.runner_url, kind), installation_token)["token"]


def run_listener(command: list[str]) -> int:
    """Run the listener, relaying a container stop to it, and return its exit status."""
    process: subprocess.Popen | None = None
    stop_requested = False

    def relay(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        if process is not None:
            process.send_signal(signal.SIGTERM)

    previous = {sig: signal.signal(sig, relay) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        # run.sh only relays SIGTERM/SIGINT to the listener when this is set; otherwise
        # a container stop kills the shell and orphans a running job.
        process = subprocess.Popen(command, env={**os.environ, "RUNNER_MANUALLY_TRAP_SIG": "1"})
        if stop_requested:
            process.send_signal(signal.SIGTERM)
        return process.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    print("Requesting runner registration token...")
    try:
        registration_token = runner_admin_token(settings, "registration-token")
    except (ConfigError, ApiError, OSError, KeyError) as error:
        print(f"Failed to obtain runner registration token: {error}", file=sys.stderr)
        return 1

    print(f"Configuring runner '{settings.name}'...")
    subprocess.run(
        [
            "./config.sh",
            "--url", settings.runner_url,
            "--token", registration_token,
            "--name", settings.name,
            "--labels", settings.labels,
            "--ephemeral",
            "--unattended",
            "--disableupdate",
            "--replace",
        ],
        check=True,
    )  # fmt: skip

    status = run_listener(["./run.sh"])

    # an ephemeral runner deletes .runner once it has run a job, so its presence means
    # the registration would otherwise linger offline until GitHub prunes it
    if Path(".runner").exists():
        print(f"Removing idle runner '{settings.name}' from GitHub...")
        try:
            remove_token = runner_admin_token(settings, "remove-token")
            subprocess.run(["./config.sh", "remove", "--token", remove_token], check=True)
        except (ApiError, OSError, KeyError, subprocess.CalledProcessError) as error:
            print(f"Failed to remove runner registration: {error}", file=sys.stderr)

    return status


if __name__ == "__main__":
    sys.exit(main())
