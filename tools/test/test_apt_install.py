import subprocess

import pytest

import apt_install


class TestCheckPackages:
    @pytest.mark.parametrize("arg", ["git", "g++", "libssl3t64", "python3.13", "curl=8.14.1-2", "jq=1.7.1-6+b1"])
    def test_accepts_package_names(self, arg):
        assert apt_install.check_packages([arg]) == [arg]

    @pytest.mark.parametrize(
        "arg",
        ["-y", "-o", "--config-file=x", "-oAPT::Update::Pre-Invoke::=sh", "Git", "a", "curl/stable", "curl;id", ""],
    )
    def test_rejects_anything_else(self, arg):
        with pytest.raises(apt_install.UsageError, match="not a package name"):
            apt_install.check_packages(["git", arg])

    def test_requires_at_least_one_package(self):
        with pytest.raises(apt_install.UsageError, match="usage"):
            apt_install.check_packages([])


class FakeAptGet:
    """Records apt-get invocations and fails the ones whose subcommand is listed in `failing`."""

    def __init__(self, monkeypatch, failing=()):
        self.commands = []
        self.failing = failing
        monkeypatch.setattr(subprocess, "run", self)

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        assert kwargs["env"] == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}
        return subprocess.CompletedProcess(command, 100 if command[1] in self.failing else 0)


class TestInstall:
    def test_updates_then_installs_without_recommends(self, monkeypatch):
        apt_get = FakeAptGet(monkeypatch)
        assert apt_install.install(["git", "jq=1.7.1-6+b1"]) == 0
        assert apt_get.commands == [
            ["apt-get", "update"],
            ["apt-get", "install", "-y", "--no-install-recommends", "--", "git", "jq=1.7.1-6+b1"],
        ]

    def test_stops_when_update_fails(self, monkeypatch):
        apt_get = FakeAptGet(monkeypatch, failing={"update"})
        assert apt_install.install(["git"]) == 100
        assert apt_get.commands == [["apt-get", "update"]]

    def test_returns_the_install_status(self, monkeypatch):
        FakeAptGet(monkeypatch, failing={"install"})
        assert apt_install.install(["git"]) == 100


class TestMain:
    def test_rejects_options_before_touching_apt(self, monkeypatch, capsys):
        apt_get = FakeAptGet(monkeypatch)
        assert apt_install.main(["-o", "Dpkg::Pre-Install-Pkgs::=id"]) == 2
        assert apt_get.commands == []
        assert "not a package name: -o Dpkg::Pre-Install-Pkgs::=id" in capsys.readouterr().err

    def test_refuses_to_run_unprivileged(self, monkeypatch, capsys):
        apt_get = FakeAptGet(monkeypatch)
        monkeypatch.setattr(apt_install.os, "geteuid", lambda: 1000)
        assert apt_install.main(["git"]) == 2
        assert apt_get.commands == []
        assert "must run as root" in capsys.readouterr().err

    def test_installs_as_root(self, monkeypatch):
        apt_get = FakeAptGet(monkeypatch)
        monkeypatch.setattr(apt_install.os, "geteuid", lambda: 0)
        assert apt_install.main(["git"]) == 0
        assert len(apt_get.commands) == 2
