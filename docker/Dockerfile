# Execution image for generated code.
#
# The runtime starts one short-lived container per `code_exec` call, mounts the
# agent workspace at /workspace and removes the container when it exits. See
# docker/README.md for why the container is short-lived rather than long-running.

FROM python:3.12-slim

# Generated code must not run as root inside the container. A fixed uid keeps the
# image reproducible; ownership does not matter on Windows bind mounts.
RUN useradd --create-home --uid 10001 runner

# pytest is part of the image so `sandbox/test_runner.py` can evaluate generated code
# inside the container instead of on the host. Versions pinned to match the dev env.
RUN pip install --no-cache-dir "pytest==9.2.0" "pytest-asyncio==1.4.1"

WORKDIR /workspace
USER runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

CMD ["python", "-c", "print('agentverse sandbox ready')"]
