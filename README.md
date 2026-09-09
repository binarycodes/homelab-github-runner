# homelab-github-runner

Ephemeral, multi-arch GitHub Actions runner image. Each container registers
itself through a GitHub App, runs exactly one job, and exits.

Image: `docker.io/binarycodes/github-runner:<runner-version>` (also `latest`),
built for `linux/amd64` and `linux/arm64`.

## GitHub App

The runner registers with a GitHub App installation instead of a personal
token. Create an App, install it on the organization or repository the runner
should serve, and grant it:

| Runner scope | Permission                                   |
|--------------|----------------------------------------------|
| organization | Organization → Self-hosted runners: read & write |
| repository   | Repository → Administration: read & write     |

Generate a private key for the App and mount the `.pem` file into the
container. The installation ID is the last path segment of the installation's
settings URL (`https://github.com/organizations/<org>/settings/installations/<id>`).

Enterprise runners are not supported: a GitHub App installation cannot
register them.

## Configuration

| Variable                  | Required | Description |
|---------------------------|----------|-------------|
| `GH_RUNNER_URL`           | yes      | `https://github.com/<org>` or `https://github.com/<owner>/<repo>` |
| `GH_APP_ID`               | yes      | GitHub App ID |
| `GH_APP_INSTALLATION_ID`  | yes      | Installation ID of the App on that org or repo |
| `GH_APP_PRIVATE_KEY_FILE` | yes      | Path inside the container to the App's private key |
| `GH_RUNNER_LABELS`        | no       | Comma-separated labels; default `self-hosted` |
| `GH_RUNNER_NAME`          | no       | Runner name; default `ephemeral-<hostname>-<random>` |

## Running

The container exits after one job, so run it under a restart policy to keep a
runner available:

```yaml
services:
  runner:
    image: docker.io/binarycodes/github-runner:latest
    restart: always
    environment:
      GH_RUNNER_URL: https://github.com/my-org
      GH_APP_ID: "123456"
      GH_APP_INSTALLATION_ID: "12345678"
      GH_APP_PRIVATE_KEY_FILE: /run/secrets/github-app.pem
      GH_RUNNER_LABELS: self-hosted,linux,homelab
    volumes:
      - ./github-app.pem:/run/secrets/github-app.pem:ro
```

Scale with multiple containers; each one registers under its own name.

Stopping the container forwards the signal to the runner. A busy runner
finishes its job first, and an idle runner removes its registration from
GitHub before exiting.

## Inside the container

Jobs run as the unprivileged user `runner` on Debian 13 with these tools
preinstalled: `ansible`, `git`, `jq`, `make`, `openssh-client`, `packer`,
`python3` (with `pip` and `venv`), `shellcheck`, `terraform`, `unzip`, `zstd`.

The only permitted `sudo` command installs Debian packages:

```sh
sudo apt-install <package>[=<version>]...
```

Anything else under `sudo` is denied.

## Verifying the image

Images are signed keyless with cosign from this repository's `build.yml` on
`main`, and carry SBOM and provenance attestations:

```sh
cosign verify docker.io/binarycodes/github-runner:latest \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity https://github.com/cloudyhomelab/homelab-github-runner/.github/workflows/build.yml@refs/heads/main
```

## License

[GNU General Public License v3.0 or later](LICENSE).
