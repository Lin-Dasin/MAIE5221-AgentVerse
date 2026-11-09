"""Host-side handoffs. B remains responsible for container isolation and mounts."""

import shutil
from pathlib import Path

from protocol.models import InputRef, Task, relative_path


def inside(root: Path, relative: str) -> Path:
    relative_path(relative)
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("file must remain inside its assigned root")
    return candidate


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("expected an ordinary artifact file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError("handoff source directory is missing")
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("handoff directories must contain ordinary files")
        if path.is_file():
            copy_file(path, inside(destination, path.relative_to(source).as_posix()))


class ArtifactStore:
    """A fresh store per run; input/output cleanup never removes memory/."""

    def __init__(self, root: Path, run_id: str):
        self.root = root.resolve()
        self.workspaces = self.root / "workspaces"
        self.archive = inside(self.root / "artifacts", run_id)
        self.snapshots = inside(self.root / ".agentverse-state", run_id)
        if self.archive.exists() or self.snapshots.exists():
            raise ValueError("run_id already has artifacts or state; use a new run_id")
        self.archive.mkdir(parents=True)
        self.snapshots.mkdir(parents=True)
        self.accepted: set[str] = set()

    def workspace(self, agent_id: str) -> Path:
        return inside(self.workspaces, agent_id)

    def _reset(self, task: Task) -> Path:
        workspace = self.workspace(task.assignee)
        workspace.mkdir(parents=True, exist_ok=True)
        for name in ("inputs", "outputs"):
            target = inside(workspace, name)
            if target.is_symlink():
                raise ValueError("workspace directories must not be symlinks")
            if target.exists():
                shutil.rmtree(target)
            target.mkdir()
        return workspace

    def prepare(self, task: Task) -> None:
        self._reset(task)
        self.handoff(task, task.inputs)

    def handoff(self, task: Task, inputs: list[InputRef]) -> None:
        for ref in inputs:
            if ref.from_task not in self.accepted:
                raise FileNotFoundError("upstream artifact has not passed acceptance")
            source = inside(self.archive / ref.from_task, ref.path)
            destination = inside(self.workspace(task.assignee), ref.destination)
            copy_file(source, destination)

    def publish(self, task: Task) -> None:
        temporary = self.archive / f".{task.task_id}.pending"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir()
        try:
            for output in task.outputs:
                source = inside(self.workspace(task.assignee), output)
                copy_file(source, inside(temporary, output.removeprefix("outputs/")))
            temporary.replace(self.archive / task.task_id)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self.accepted.add(task.task_id)

    def suspend(self, task: Task) -> None:
        snapshot = self.snapshots / task.task_id
        if snapshot.exists():
            shutil.rmtree(snapshot)
        for name in ("inputs", "outputs"):
            copy_tree(inside(self.workspace(task.assignee), name), snapshot / name)

    def resume(self, task: Task) -> None:
        snapshot = self.snapshots / task.task_id
        self._reset(task)
        for name in ("inputs", "outputs"):
            copy_tree(snapshot / name, inside(self.workspace(task.assignee), name))

    def child_inputs(self, children: list[Task]) -> list[InputRef]:
        return [
            InputRef(
                from_task=child.task_id,
                path=output.removeprefix("outputs/"),
                destination=f"inputs/delegated/{child.task_id}/{output.removeprefix('outputs/')}",
            )
            for child in children
            if child.task_id in self.accepted
            for output in child.outputs
        ]

    def discard_snapshot(self, task_id: str) -> None:
        shutil.rmtree(self.snapshots / task_id, ignore_errors=True)
