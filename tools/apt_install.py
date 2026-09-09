#!/usr/bin/env python3
"""Install Debian packages as root from an unprivileged workflow step.

This is the only command the runner user may run through sudo. A bare apt-get would
accept -o and Dpkg::Pre-Install-Pkgs, which run arbitrary commands as root.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence

# Debian policy package names, optionally pinned to a version; anything else,
# including every option, is rejected
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]+(=[a-zA-Z0-9.+~:-]+)?$")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}


class UsageError(Exception):
    pass


def check_packages(args: Sequence[str]) -> list[str]:
    if not args:
        raise UsageError("usage: sudo apt-install <package>[=<version>]...")
    rejected = [arg for arg in args if not PACKAGE.match(arg)]
    if rejected:
        raise UsageError(f"not a package name: {' '.join(rejected)}")
    return list(args)


def install(packages: Sequence[str]) -> int:
    for command in (
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "--no-install-recommends", "--", *packages],
    ):
        status = subprocess.run(command, env=ENV).returncode
        if status != 0:
            return status
    return 0


def main(args: Sequence[str]) -> int:
    try:
        packages = check_packages(args)
    except UsageError as error:
        print(error, file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("apt-install must run as root: sudo apt-install ...", file=sys.stderr)
        return 2
    return install(packages)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
