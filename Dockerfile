ARG RUNNER_VERSION="unknown"
ARG RUNNER_CHECKSUM_X64="unknown"
ARG RUNNER_CHECKSUM_ARM64="unknown"
ARG RUNNER_USER="runner"

# fetch the runner natively; only the tarball is arch-specific
FROM --platform=$BUILDPLATFORM debian:13-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS runner
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG TARGETARCH
ARG RUNNER_VERSION
ARG RUNNER_CHECKSUM_X64
ARG RUNNER_CHECKSUM_ARM64

# buildx reports amd64; the runner names that tarball x64
RUN [ "${RUNNER_VERSION}" != "unknown" ] || { echo "ERROR: RUNNER_VERSION is not set"; exit 2; }; \
    case "${TARGETARCH:-}" in \
        amd64) runner_arch="x64";   checksum="${RUNNER_CHECKSUM_X64}" ;; \
        arm64) runner_arch="arm64"; checksum="${RUNNER_CHECKSUM_ARM64}" ;; \
        *) echo "ERROR: unsupported TARGETARCH '${TARGETARCH:-}' (build with buildx)"; exit 2 ;; \
    esac; \
    [ "${checksum}" != "unknown" ] || { echo "ERROR: RUNNER_CHECKSUM for ${runner_arch} is not set"; exit 2; }; \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL --retry 3 --retry-all-errors -o actions-runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${runner_arch}-${RUNNER_VERSION}.tar.gz" \
    && printf '%s  actions-runner.tar.gz\n' "${checksum#sha256:}" | sha256sum -c - \
    && mkdir /actions-runner \
    && tar xzf actions-runner.tar.gz -C /actions-runner


FROM debian:13-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG RUNNER_USER

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gpg lsb-release \
    && curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg - \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" > /etc/apt/sources.list.d/hashicorp.list \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    ansible \
    git \
    jq \
    make \
    openssh-client \
    openssl \
    packer \
    python3 \
    python3-pip \
    python3-venv \
    shellcheck \
    sudo \
    terraform \
    unzip \
    && apt-get -y autoremove \
    && apt-get autoclean \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# create non-root user
RUN useradd -m -s /bin/bash "${RUNNER_USER}" \
    && usermod -aG sudo "${RUNNER_USER}" \
    && printf '%s\n' \
        'Defaults env_keep += "DEBIAN_FRONTEND"' \
        "${RUNNER_USER} ALL=(ALL) NOPASSWD: /usr/bin/apt-get" > /etc/sudoers.d/10-runner-conf \
    && chmod 0440 /etc/sudoers.d/10-runner-conf \
    && visudo -cf /etc/sudoers.d/10-runner-conf

COPY --from=runner --chown=${RUNNER_USER}:${RUNNER_USER} /actions-runner "/home/${RUNNER_USER}/actions-runner"
RUN "/home/${RUNNER_USER}/actions-runner/bin/installdependencies.sh" \
    && apt-get -y autoremove \
    && apt-get autoclean \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=0755 entrypoint.sh /entrypoint.sh

USER "${RUNNER_USER}"
WORKDIR "/home/${RUNNER_USER}/actions-runner"

ENTRYPOINT ["/entrypoint.sh"]
