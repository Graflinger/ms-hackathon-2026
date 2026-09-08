FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/
RUN mkdir -p /var/lib/goldenloop /workspaces/goldenloop/.venv /workspaces/goldenloop/apps/web/node_modules \
    && chown -R vscode:vscode /var/lib/goldenloop /workspaces/goldenloop

USER vscode
