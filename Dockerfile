# syntax=docker/dockerfile:1.6
# Combined GARM controller + Proxmox external provider
# This image runs garm as the primary process and bundles the provider binary.

FROM golang:1.22-alpine AS garm-build
ARG GARM_VERSION=v0.1.6

RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch ${GARM_VERSION} https://github.com/cloudbase/garm.git .
RUN go build -o /out/garm ./cmd/garm

FROM ghcr.io/astral-sh/uv:0.4.28-python3.12-alpine AS provider-build
WORKDIR /src
COPY pyproject.toml uv.lock /src/
RUN uv sync --frozen --no-dev
COPY src /src/src
RUN uv build --wheel
RUN uv pip install --system /src/dist/*.whl

FROM python:3.14-alpine
LABEL org.opencontainers.image.source="https://github.com/cloudbase/garm"
LABEL org.opencontainers.image.title="garm-proxmox-combined"
LABEL org.opencontainers.image.description="GARM controller with bundled Proxmox external provider"

RUN apk add --no-cache ca-certificates tini openssl libffi
WORKDIR /opt/garm

# GARM binary
COPY --from=garm-build /out/garm /usr/local/bin/garm

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
ENTRYPOINT ["/sbin/tini","--"]
CMD ["garm","-config","/etc/garm/config.toml"]
