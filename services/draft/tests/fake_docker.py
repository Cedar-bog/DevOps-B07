"""Script Docker commands while exercising the real pipeline orchestration."""

from collections import deque
from pathlib import Path

from draft.docker import CommandResult

IMAGE_ID = "sha256:" + "a" * 64
MISSING_MAKE = CommandResult(1, "#8 [4/4] RUN make\n", "/bin/sh: 1: make: not found\n", 0.1)
BUILD_OK = CommandResult(0, "#8 [4/4] RUN make\n#8 DONE\n", "", 0.1)
RUN_OK = CommandResult(0, "hello E3\n", "", 0.1)


class FakeDocker:
    def __init__(self, builds, runs=(), *, inspect_ok=True, cleanup_ok=True):
        self.builds = deque(builds)
        self.runs = deque(runs)
        self.inspect_ok = inspect_ok
        self.cleanup_ok = cleanup_ok
        self.commands = []
        self.dockerfiles = []

    def __call__(self, command, cwd, timeout):
        self.commands.append(command)
        if command[1] == "build":
            self.dockerfiles.append((cwd / command[command.index("-f") + 1]).read_text(encoding="utf-8"))
            assert self.builds, "unexpected build"
            return self.builds.popleft()
        if command[1:3] == ["image", "inspect"]:
            return CommandResult(0 if self.inspect_ok else 1, IMAGE_ID if self.inspect_ok else "", "", 0.01)
        if command[1] == "run":
            Path(command[command.index("--cidfile") + 1]).write_text("fake-container-id\n", encoding="utf-8")
            assert self.runs, "unexpected verification"
            return self.runs.popleft()
        if command[1] in {"rm", "rmi"}:
            return CommandResult(0 if self.cleanup_ok else 1, "", "", 0.01)
        raise AssertionError(f"unexpected Docker command: {command}")
