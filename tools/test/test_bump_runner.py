import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import bump_runner

X64 = "70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613"
ARM64 = "9b1dc70626422526e3c94767cf024896beb15da5342a3f4819bf2feac13e0393"
NOTES = f"""## SHA-256 Checksums

- actions-runner-osx-x64-2.338.0.tar.gz <!-- BEGIN SHA osx-x64 -->{"a" * 64}<!-- END SHA osx-x64 -->
- actions-runner-linux-x64-2.338.0.tar.gz <!-- BEGIN SHA linux-x64 -->{X64}<!-- END SHA linux-x64 -->
- actions-runner-linux-arm64-2.338.0.tar.gz <!-- BEGIN SHA linux-arm64 -->{ARM64}<!-- END SHA linux-arm64 -->
"""
PAYLOAD = {"tag_name": "v2.338.0", "body": NOTES}
RELEASE = bump_runner.Release("2.338.0", {"linux-x64": X64, "linux-arm64": ARM64})

BAKE = """variable "REGISTRY" { default = "docker.io" }

variable "GH_RUNNER_VERSION" { default = "2.337.0" }
variable "GH_RUNNER_CHECKSUM_X64"   { default = "sha256:old-x64" }
variable "GH_RUNNER_CHECKSUM_ARM64" { default = "sha256:old-arm64" }

target "image" {
  args = { RUNNER_VERSION = GH_RUNNER_VERSION }
}
"""


class TestRelease:
    def test_keeps_the_version_and_the_checksums_the_image_needs(self):
        assert bump_runner.Release.from_api(PAYLOAD) == RELEASE

    @pytest.mark.parametrize("tag", ["2.338.0", "v2.338", "v2.338.0-rc1", "", "main"])
    def test_rejects_unexpected_tags(self, tag):
        with pytest.raises(bump_runner.ApiError, match="unexpected release tag"):
            bump_runner.Release.from_api({"tag_name": tag, "body": NOTES})

    def test_requires_both_linux_checksums(self):
        body = NOTES.replace("linux-arm64", "linux-arm")
        with pytest.raises(bump_runner.ApiError, match="no checksum for: linux-arm64"):
            bump_runner.Release.from_api({"tag_name": "v2.338.0", "body": body})

    def test_tolerates_a_missing_body(self):
        with pytest.raises(bump_runner.ApiError, match="linux-x64 linux-arm64"):
            bump_runner.Release.from_api({"tag_name": "v2.338.0", "body": None})

    def test_bake_variables_prefix_the_digest_algorithm(self):
        assert RELEASE.bake_variables() == {
            "GH_RUNNER_VERSION": "2.338.0",
            "GH_RUNNER_CHECKSUM_X64": f"sha256:{X64}",
            "GH_RUNNER_CHECKSUM_ARM64": f"sha256:{ARM64}",
        }


class TestFetchLatestRelease:
    def test_sends_the_api_headers_and_parses_the_release(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["request"] = request
            return io.BytesIO(json.dumps(PAYLOAD).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert bump_runner.fetch_latest_release() == RELEASE
        request = seen["request"]
        assert request.full_url == bump_runner.RELEASES_URL
        assert request.get_method() == "GET"
        assert request.get_header("Accept") == "application/vnd.github+json"
        assert not request.has_header("Authorization")

    def test_uses_the_token_when_given(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["request"] = request
            return io.BytesIO(b'{"tag_name": "v2.338.0", "body": ""}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(bump_runner.ApiError):
            bump_runner.fetch_latest_release("ghp_x")
        assert seen["request"].get_header("Authorization") == "Bearer ghp_x"

    def test_http_error_includes_status_and_body(self, monkeypatch):
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"rate limited"))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(bump_runner.ApiError, match="HTTP 403: rate limited"):
            bump_runner.fetch_latest_release()

    def test_connection_error_is_reported(self, monkeypatch):
        def fake_urlopen(request, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(bump_runner.ApiError, match="connection refused"):
            bump_runner.fetch_latest_release()


class TestBakeVariables:
    def test_reads_a_default(self):
        assert bump_runner.read_variable(BAKE, "GH_RUNNER_VERSION") == "2.337.0"
        assert bump_runner.read_variable(BAKE, "GH_RUNNER_CHECKSUM_ARM64") == "sha256:old-arm64"

    def test_replaces_only_the_default_and_keeps_the_layout(self):
        text = bump_runner.set_variable(BAKE, "GH_RUNNER_CHECKSUM_X64", "sha256:new")
        assert 'variable "GH_RUNNER_CHECKSUM_X64"   { default = "sha256:new" }' in text
        assert text.replace('"sha256:new"', '"sha256:old-x64"') == BAKE

    def test_does_not_match_a_variable_with_the_same_prefix(self):
        with pytest.raises(bump_runner.BakeError, match="found 0"):
            bump_runner.read_variable(BAKE, "GH_RUNNER")

    def test_refuses_an_ambiguous_variable(self):
        with pytest.raises(bump_runner.BakeError, match="found 2"):
            bump_runner.set_variable(BAKE + BAKE, "GH_RUNNER_VERSION", "1")

    def test_parses_the_repository_bake_file(self):
        text = (Path(__file__).parents[2] / "docker-bake.hcl").read_text()
        assert bump_runner.TAG.match("v" + bump_runner.read_variable(text, "GH_RUNNER_VERSION"))
        for name in bump_runner.CHECKSUM_VARIABLES.values():
            assert bump_runner.read_variable(text, name).startswith("sha256:")


class TestBump:
    def test_rewrites_all_three_variables(self, tmp_path):
        bake_file = tmp_path / "docker-bake.hcl"
        bake_file.write_text(BAKE)
        assert bump_runner.bump(bake_file, RELEASE) == "2.337.0"
        text = bake_file.read_text()
        assert bump_runner.read_variable(text, "GH_RUNNER_VERSION") == "2.338.0"
        assert bump_runner.read_variable(text, "GH_RUNNER_CHECKSUM_X64") == f"sha256:{X64}"
        assert bump_runner.read_variable(text, "GH_RUNNER_CHECKSUM_ARM64") == f"sha256:{ARM64}"
        assert text.count("\n") == BAKE.count("\n")

    def test_leaves_a_current_file_untouched(self, tmp_path):
        bake_file = tmp_path / "docker-bake.hcl"
        bake_file.write_text(BAKE)
        before = bake_file.stat().st_mtime_ns
        assert bump_runner.bump(bake_file, bump_runner.Release("2.337.0", RELEASE.checksums)) is None
        assert bake_file.read_text() == BAKE
        assert bake_file.stat().st_mtime_ns == before


class TestMain:
    def test_prints_the_new_version_on_stdout(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(bump_runner, "fetch_latest_release", lambda token: RELEASE)
        bake_file = tmp_path / "docker-bake.hcl"
        bake_file.write_text(BAKE)
        assert bump_runner.main([str(bake_file)]) == 0
        out, err = capsys.readouterr()
        assert out == "2.338.0\n"
        assert "2.337.0 -> 2.338.0" in err

    def test_prints_nothing_when_current(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(bump_runner, "fetch_latest_release", lambda token: RELEASE)
        bake_file = tmp_path / "docker-bake.hcl"
        bake_file.write_text(BAKE.replace("2.337.0", "2.338.0"))
        assert bump_runner.main([str(bake_file)]) == 0
        out, err = capsys.readouterr()
        assert out == ""
        assert "already at runner 2.338.0" in err

    def test_passes_the_token_from_the_environment(self, monkeypatch, tmp_path):
        seen = {}

        def fetch(token):
            seen["token"] = token
            return RELEASE

        monkeypatch.setenv("GITHUB_TOKEN", "ghs_y")
        monkeypatch.setattr(bump_runner, "fetch_latest_release", fetch)
        bake_file = tmp_path / "docker-bake.hcl"
        bake_file.write_text(BAKE)
        bump_runner.main([str(bake_file)])
        assert seen["token"] == "ghs_y"

    def test_api_failure_exits_1_without_touching_the_file(self, monkeypatch, tmp_path, capsys):
        def fail(token):
            raise bump_runner.ApiError("HTTP 500")

        monkeypatch.setattr(bump_runner, "fetch_latest_release", fail)
        bake_file = tmp_path / "docker-bake.hcl"
        bake_file.write_text(BAKE)
        assert bump_runner.main([str(bake_file)]) == 1
        assert bake_file.read_text() == BAKE
        assert "HTTP 500" in capsys.readouterr().err

    def test_missing_bake_file_exits_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(bump_runner, "fetch_latest_release", lambda token: RELEASE)
        assert bump_runner.main([str(tmp_path / "missing.hcl")]) == 1
        assert "missing.hcl" in capsys.readouterr().err
