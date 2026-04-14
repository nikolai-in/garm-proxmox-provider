# syntax=docker/dockerfile:1.6
# Combined GARM controller + Proxmox external provider
# This image runs garm as the primary process and bundles the provider binary.

FROM golang:1.23-bookworm AS garm-build
ARG GARM_VERSION=v0.1.6

RUN apt-get update && apt-get install -y git curl tar build-essential nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 --branch ${GARM_VERSION} https://github.com/cloudbase/garm.git .
RUN cd webapp && npm install && npm run build && \
    rm -rf assets/_app assets/assets assets/index.html assets/*.png && \
    cp -r build/* assets/ || true
RUN CGO_ENABLED=1 make build

FROM ghcr.io/astral-sh/uv:debian-slim AS provider-build
WORKDIR /src
COPY pyproject.toml uv.lock README.md /src/
RUN uv sync --frozen --no-dev --no-install-project
COPY src /src/src
RUN uv build --wheel

FROM python:3.14-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/cloudbase/garm"
LABEL org.opencontainers.image.title="garm-proxmox-combined"
LABEL org.opencontainers.image.description="GARM controller with bundled Proxmox external provider"

RUN apt-get update && apt-get install -y ca-certificates tini openssl libffi8 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/garm

# GARM binary
COPY --from=garm-build /src/bin/garm /usr/local/bin/garm
COPY --from=garm-build /src/bin/garm-cli /usr/local/bin/garm-cli

# Provider install (wheel built in provider stage)
COPY --from=provider-build /src/dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl \
  && rm -f /tmp/*.whl

# Provider location expected by garm external provider config
RUN mkdir -p /opt/garm/providers.d \
  && ln -s /usr/local/bin/garm-proxmox-provider /opt/garm/providers.d/garm-proxmox-provider

# Default config dir (mount /etc/garm as a volume at runtime)
VOLUME ["/etc/garm"]

EXPOSE 80
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["garm","-config","/etc/garm/config.toml"]
