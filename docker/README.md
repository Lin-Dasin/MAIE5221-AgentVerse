# Sandbox container

Execution image for generated code, plus the rationale for how the runtime uses it.

## Build

```bash
docker build -t agentverse-sandbox:latest docker/
```

## Verify

```bash
docker run --rm --network none --memory 256m agentverse-sandbox:latest
# -> agentverse sandbox ready
```

## Use it from the runtime

```bash
export CODE_EXEC_BACKEND=docker
export CODE_EXEC_IMAGE=agentverse-sandbox:latest   # optional, this is the default
```

Unset `CODE_EXEC_BACKEND` (or set it to `subprocess`) to keep the host interpreter.
The default is `subprocess`, so a machine without Docker still runs the test suite and
the offline harness.

## Short-lived containers, not one per agent

The project plan describes one long-running container per agent. The implementation
starts **one container per `code_exec` call** and removes it on exit. This is a
deliberate deviation:

| | Long-running per agent | Short-lived per call (implemented) |
|---|---|---|
| Start-up cost | paid once | ~0.5 s per call |
| State between calls | needs lifecycle management | none — the workspace mount is the only state |
| Crash recovery | must detect and rebuild | not needed: a crashed container is simply absent |
| Isolation between tasks of one agent | weaker (same container) | stronger (fresh filesystem each call) |

The properties that the specification asks for — effective isolation, resource limits,
no host network — are met by the short-lived form. What is lost is container warm-up
time, which no dev-set case is sensitive to. If a later milestone needs warm containers,
the backend switch is already in place: change `_command()` in `tools/code_exec.py`.

## What the container enforces

| Control | Value |
|---|---|
| Network | `--network none` |
| Memory | `--memory 512m` |
| CPU | `--cpus 1` |
| User | uid 10001, non-root |
| Filesystem | only the agent workspace, mounted at `/workspace` |
| Lifecycle | `--rm`, plus an explicit `docker rm -f` on timeout |

## What it still does not enforce

- No read-only root filesystem and no seccomp profile.
- No per-run image pinning by digest, so a rebuilt `:latest` changes behaviour silently.
- Generated code can still exhaust the 512 MB and be OOM-killed; that surfaces as exit
  code 137 rather than a friendly tool error.

## How this is tested

`tests/test_code_exec.py` contains five container cases: it runs code, asserts host
paths are invisible, asserts the network is unreachable, asserts a timeout leaves no
container behind, and rejects an unknown backend. They are skipped automatically when
`docker info` fails or the image is missing, so CI without a daemon stays green.
