FROM debian:stable-slim

# Base deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl ca-certificates git jq \
    && rm -rf /var/lib/apt/lists/*

ARG RUNNER_VERSION=2.319.1

# Create non-root user + home
RUN useradd -m -d /home/runner -s /bin/bash runner

# Install GitHub Actions runner as root
WORKDIR /home/runner/actions-runner

RUN curl -L -o actions-runner.tar.gz \
      https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
    && tar xzf actions-runner.tar.gz \
    && rm actions-runner.tar.gz \
    && ./bin/installdependencies.sh

# Copy entrypoint, then fix ownership
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
    chown -R runner:runner /home/runner && \
    chown runner:runner /entrypoint.sh

# Runtime: non-root user
USER runner
WORKDIR /home/runner/actions-runner

ENTRYPOINT ["/entrypoint.sh"]
