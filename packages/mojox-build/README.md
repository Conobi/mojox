# mojox-build

PEP 517 + PEP 660 build backend that compiles Mojo libraries into platform-tagged wheels.

Declare it in `[build-system]` and let `uv build` or `pip wheel` handle the rest.

## Quickstart

```toml
# pyproject.toml
[project]
name = "boucle"
version = "0.2.0"
description = "Async event loop primitives for Mojo"
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.10"
dependencies = ["mojox", "mojo-compiler>=1.0,<2"]

[build-system]
requires      = ["mojox-build>=0.4", "mojo-compiler>=1.0,<2"]
build-backend = "mojox_build"

[tool.mojox]
packages = ["boucle"]
```

```bash
uv build       # -> dist/boucle-0.2.0-py3-none-manylinux_2_43_x86_64.whl
uv publish     # uploads to PyPI
```

The wheel places compiled `.mojoc` packages under `mojo_packages/`, which `mojox` discovers at runtime.

## `[tool.mojox]` reference

The full `[tool.mojox]` table is owned by `mojox-core` and shared with the CLI. Keys relevant to the build backend:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `package-root` | str | `"src"` | Directory containing top-level package dirs to compile |
| `packages` | list[str] | (auto-scan `package-root`) | Explicit list of source directories. Each becomes one `.mojoc` |
| `build-profile` | str | `"release"` | Which profile to use for wheel builds |
| `native-libs` | list[str] | `[]` | Pre-built `.so`/`.dylib` files to copy into `mojo_packages/lib/` |
| `defines` | table | `{}` | `-D KEY=VALUE` flags passed to `mojo precompile` |
| `flags` | list[str] | `[]` | Extra flags appended to every compile invocation |
| `source-include` | list[str] | (sensible default) | Glob patterns of files to include in the sdist |
| `source-exclude` | list[str] | `[]` | Glob patterns to exclude from the sdist |
| `wheel-exclude` | list[str] | `[]` | Glob patterns to exclude from the wheel |

## How it works

1. A PEP 517 frontend (`uv build`, `pip wheel`) calls `build_wheel()`, `build_sdist()`, or `build_editable()`.
2. The hook reads `pyproject.toml` via `mojox-core`'s manifest parser.
3. Policy is resolved from the active build profile (defaults to `release`).
4. `mojo precompile` runs for each package directory, producing `.mojoc` files.
5. The compiled packages, native libs, and metadata are assembled into a wheel tagged with the host platform.

## Architecture

Five modules, roughly 1100 lines total:

- `__init__.py` re-exports the PEP 517 hook surface.
- `_hooks.py` implements PEP 517 + PEP 660 hooks (thin glue that wires config, preflight, and build together).
- `_build.py` handles compilation, wheel assembly, sdist assembly, and editable installs.
- `_metadata.py` renders PEP 621 / PEP 643 METADATA and PEP 427 WHEEL files.
- `_preflight.py` validates the build environment before compilation starts.

Config parsing, policy resolution, and toolchain detection live in `mojox-core`. The only runtime dependency beyond the stdlib is `packaging` (for platform tags).

## Features

- **Platform-tagged wheels.** Compiled packages are native code. Wheels are tagged with the host platform (`manylinux_2_34_x86_64`, `macosx_13_0_arm64`, etc.), so cross-platform installs are correctly rejected by the resolver.
- **Native lib bundling.** List `.so`/`.dylib` paths in `native-libs`. They ship in `mojo_packages/lib/`, where `mojox` adds them to `LD_LIBRARY_PATH` at runtime.
- **PEP 660 editable installs.** `uv pip install -e .` works. For rebuild-on-change semantics, add `cache-keys = [{ file = "pyproject.toml" }, { file = "**/*.mojo" }]` to `[tool.uv]`.
- **Per-distribution editable files.** Two mojox projects installed editable into the same venv do not clobber each other.
- **Reproducible builds.** ZIP and tar timestamps respect `SOURCE_DATE_EPOCH`.
- **Parallel compilation.** Multi-package repos compile concurrently, capped at 8 workers.
- **Preflight checks.** Missing `mojo`, missing dirs, missing native libs, bad config -- one clean error message, not a compiler stderr dump.
- **Full PEP 621 / PEP 639 metadata.** Authors, maintainers, urls, keywords, classifiers, optional-dependencies, license-files all flow into wheel METADATA.
- **Compiler pin.** When packages are compiled, `Requires-Dist: mojo-compiler==<version>` is emitted in the wheel METADATA so the resolver enforces compiler compatibility.
- **Content-based platform tags.** Wheels with native libs or binaries get a platform tag; pure-metadata wheels stay `any`.
- **`--config-setting verbose=true`.** Streams compiler output during builds for debugging.

## Limitations

- **Dynamic `project.version`** is not supported. Declare it statically.
- **Editable installs** rebuild the full wheel on every invocation. There is no source-import fast path because the package is compiled bytecode.
- **Cross-compilation** is not exposed. Wheels are host-platform only.

## See also

- [mojox-core](https://github.com/Conobi/mojox/tree/main/packages/mojox-core) -- manifest parsing, policy resolution, toolchain detection
- [mojox](https://github.com/Conobi/mojox/tree/main/packages/mojox) -- CLI for running, testing, and managing Mojo projects

## License

MIT
