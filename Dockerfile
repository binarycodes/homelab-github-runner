ARG RUNNER_VERSION="unknown"
ARG RUNNER_USER="runner"

FROM debian:13-slim

ENV DEBIAN_FRONTEND=noninteractive
ARG RUNNER_VERSION
ARG RUNNER_USER

# Base deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    jq \
    make \
    libicu76 \
    libicu-dev \
    libssl-dev && \
    apt-get autoremove && \
    apt-get autoclean && \
    apt-get clean


# Create non-root user + home
RUN useradd -m -s /bin/bash "${RUNNER_USER}"

# Install GitHub Actions runner as root
WORKDIR "/home/${RUNNER_USER}/actions-runner"

RUN curl -L -o actions-runner.tar.gz \
      https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
    && tar xzf actions-runner.tar.gz \
    && rm actions-runner.tar.gz \
    && ./bin/installdependencies.sh

# Copy entrypoint, then fix ownership
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
    chown "${RUNNER_USER}":"${RUNNER_USER}" /entrypoint.sh

# Runtime: non-root user
USER "${RUNNER_USER}"
WORKDIR "/home/${RUNNER_USER}/actions-runner"

ENTRYPOINT ["/entrypoint.sh"]
