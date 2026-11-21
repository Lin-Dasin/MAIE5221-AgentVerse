# Execution image for generated code.
#
# The runtime starts one short-lived container per `code_exec` call, mounts the
# agent workspace at /workspace and removes the container when it exits. See
# docker/README.md for why the container is short-lived rather than long-running.

FROM python:3.12-slim

# Generated code must not run as root inside the container. A fixed uid keeps the
# image reproducible; ownership does not matter on Windows bind mounts.
RUN useradd --create-home --uid 10001 runner

WORKDIR /workspace
USER runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

CMD ["python", "-c", "print('agentverse sandbox ready')"]
