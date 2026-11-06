"""Role B: tool execution and sandbox isolation.

`SandboxToolExecutor` is the runtime adapter for `protocol.ports.ToolExecutor`;
`paths` holds the single path-validation helper every tool must route through.
"""

from sandbox.paths import PathViolation, resolve_within

__all__ = ["PathViolation", "resolve_within"]
