# mojox-core

Pure model layer for the [mojox](https://github.com/Conobi/mojox) build system.
No I/O, no subprocess, no filesystem access at module level.
All data types are frozen dataclasses; every transformer is a pure function
(data in, data out).

## Install

```bash
uv add mojox-core
```

Requires Python 3.10+. Only runtime dependency is `packaging>=23.0`
(plus `tomli` on Python < 3.11).

## Pipeline

The core exposes a five-stage pipeline where each step takes immutable data
and returns immutable data:

```
parse  ->  discover  ->  resolve  ->  plan  ->  serialize
```

I/O readers live in the separate `mojox_core.io` subpackage. Consumers can
skip them entirely and feed fixtures directly into the pipeline.

## Quick start

```python
from pathlib import Path

from mojox_core import (
    parse_manifest, discover, resolve, plan, serialize,
    LocalSettings,
)
from mojox_core.io.manifest import read as read_manifest
from mojox_core.io.toolchain import resolve as resolve_toolchain
from mojox_core.io.environment import (
    read_distributions, read_lockfile, read_host_facts,
)
from mojox_core import build_env

# 1. Read raw TOML (I/O)
raw = read_manifest(Path("."))

# 2. Parse manifest (pure)
manifest = parse_manifest(raw)

# 3. Discover targets by convention (pure)
targets = discover(manifest, Path("."))

# 4. Resolve build policy from all precedence layers (pure)
policy = resolve(manifest, "dev", {}, LocalSettings.EMPTY)

# 5. Detect toolchain and build environment (I/O)
toolchain = resolve_toolchain()
dists = read_distributions()
lock = read_lockfile(Path("."))
env = build_env(dists, lock, toolchain.mojo_path, toolchain.version)
host = read_host_facts(Path("."))

# 6. Plan mojo invocations (pure)
commands = plan(targets, env, policy, toolchain, host)

for cmd in commands:
    print(cmd.kind, cmd.argv)

# 7. Serialize to the `mojox metadata` JSON schema (pure)
metadata = serialize(targets, env, policy, commands, toolchain, diagnostics=())
```

## Public API

### Types

All exported from `mojox_core`. Every type is a frozen dataclass or enum.

| Type | Description |
|------|-------------|
| `Manifest` | Parsed `[tool.mojox]` config and PEP 621 project metadata |
| `Command` | A planned mojo invocation (data, not execution) |
| `CommandKind` | Enum of command kinds |
| `Policy` | Resolved build policy (profile + defines + flags) |
| `Profile` | Build profile (`dev` or `release`) with optimization/debug settings |
| `Target` | A single build/test/example/binary target |
| `TargetKind` | Enum of target kinds |
| `TargetGraph` | The full set of discovered targets |
| `ResolvedEnv` | Resolved environment (import paths, library paths, mojo binary) |
| `Toolchain` | Detected Mojo toolchain info |
| `HostFacts` | Host platform facts |
| `LocalSettings` | Parsed `.mojox/config.toml` settings |
| `Diagnostic` | Lint diagnostic |
| `LintConfig` | Lint configuration |
| `DistEntry` | Distribution metadata entry |
| `DistKind` | Enum of distribution kinds |
| `BinaryEntry` | Binary entry in a distribution |

### Transformers

Pure functions. No side effects, no I/O.

```python
parse_manifest(data: dict) -> Manifest
discover(manifest: Manifest, root: Path) -> TargetGraph
resolve(manifest: Manifest, profile_name: str, cli_overrides: dict, settings: LocalSettings) -> Policy
plan(graph: TargetGraph, env: ResolvedEnv, policy: Policy, toolchain: Toolchain, host: HostFacts) -> tuple[Command, ...]
build_env(dists: list[dict], lock_data: dict | None, mojo_path: str, mojo_version: str) -> ResolvedEnv
serialize(graph: TargetGraph, env: ResolvedEnv, policy: Policy, commands: tuple[Command, ...], toolchain: Toolchain, diagnostics: tuple[Diagnostic, ...]) -> dict
parse_settings(user: dict | None, project: dict | None, env: dict[str, str]) -> LocalSettings
```

### I/O readers

In the `mojox_core.io` subpackage. These are the only functions that touch
the filesystem or run subprocesses.

```python
mojox_core.io.manifest.read(path: Path) -> dict                    # read & parse pyproject.toml
mojox_core.io.toolchain.resolve() -> Toolchain                     # detect installed Mojo compiler
mojox_core.io.environment.read_distributions(platlib=None) -> list[dict]  # scan installed dists
mojox_core.io.environment.read_lockfile(project_root: Path) -> dict | None
mojox_core.io.environment.read_host_facts(manifest_dir: Path) -> HostFacts
```

### Errors

```python
class ConfigError(Exception):
    key_path: str   # dotted path to the offending key
    message: str    # human-readable explanation
```

Raised on invalid manifest or settings. No `KeyError` or `TypeError` reaches
the caller -- every rejection is a `ConfigError` with a structured key path.

## Testing without I/O

Because the pipeline is pure, you can test any stage by constructing fixtures
directly:

```python
from mojox_core import Manifest, discover, TargetGraph

manifest = Manifest(...)  # build the frozen dataclass by hand
graph = discover(manifest, some_temp_dir)
assert isinstance(graph, TargetGraph)
```

No mojo compiler, no installed packages, no pyproject.toml required.

## See also

- [mojox](https://github.com/Conobi/mojox) -- CLI that drives the full build
- [mojox-build](https://github.com/Conobi/mojox/tree/main/packages/mojox-build) -- PEP 517 build backend

## License

MIT
