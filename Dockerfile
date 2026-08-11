# FWS — Fairino Web Services
#
# Multi-arch: aarch64 matters, this is designed to run on a Raspberry Pi CM5
# wired to the robot. Build for your target explicitly:
#   docker buildx build --platform linux/arm64,linux/amd64 -t fws .
#
# Runs as a non-root user. A gateway that reaches a controller running telnet
# and unauthenticated root qconn should not itself be root.

FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY fws ./fws
RUN pip install --no-cache-dir --upgrade pip build \
 && python -m build --wheel --outdir /dist

FROM python:3.12-slim
LABEL org.opencontainers.image.title="FWS — Fairino Web Services" \
      org.opencontainers.image.description="REST + WebSocket gateway for Fairino collaborative robots" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN useradd --system --create-home --uid 10001 fws
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

USER fws
WORKDIR /home/fws

# Loopback by default, as everywhere else in FWS. To expose the port you must
# both publish it AND configure authentication -- FWS refuses to bind a
# non-loopback address without keys.
ENV FWS_SERVER__BIND_HOST=127.0.0.1 \
    FWS_SERVER__PORT=8000 \
    FWS_SERVER__DATA_DIR=/home/fws/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/system/health', timeout=3).status==200 else 1)"

ENTRYPOINT ["fws"]
