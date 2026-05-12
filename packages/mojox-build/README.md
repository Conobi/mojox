# mojox-build

PEP 517 + PEP 660 build backend that compiles a Mojo library into a `.whl`.

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
dependencies = ["mojox", "mojo-compiler>=0.26,<0.27"]

[build-system]
requires      = ["mojox-build>=0.2", "mojo-compiler>=0.26,<0.27"]
build-backend = "mojox_build"

[tool.mojox-build]
# Either a src/ layout (default):
package-root = "src"
# Or an explicit list of packages at the repo root:
# packages = ["boucle"]
```

`uv build` produces `dist/boucle-0.2.0-py3-none-<platform>.whl` containing `boucle.mojopkg` at `boucle-0.2.0.data/platlib/mojo_packages/boucle.mojopkg`. When installed, it lands in the venv's `site-packages/mojo_packages/`, which `mojox` discovers automatically.

## `[tool.mojox-build]` reference

| Key | Type | Default | Purpose |
|---|---|---|---|
| `package-root` | str | `"src"` | Directory containing top-level package dirs to compile. |
| `packages` | list[str] | (auto-scan `package-root`) | Explicit list of source directories. Each becomes one `.mojopkg`. |
| `native-libs` | list[str] | `[]` | Pre-built `.so` / `.dylib` files to copy into `mojo_packages/lib/`. |
| `defines` | table | `{}` | `-D KEY=VALUE` flags passed to `mojo package`. |
| `flags` | list[str] | `[]` | Extra flags appended to every `mojo package` invocation. |
| `source-include` | list[str] | (sensible default) | Glob patterns of files to include in the **sdist**. |
| `source-exclude` | list[str] | `[]` | Glob patterns to exclude from the sdist. |
| `wheel-exclude` | list[str] | `[]` | Glob patterns to exclude from the **wheel**. |

## What you get

- **Platform-tagged wheels.** `.mojopkg` is compiled native code, so wheels are tagged with the host platform (e.g. `manylinux_2_34_x86_64`, `macosx_13_0_arm64`). Cross-platform installs are correctly rejected by uv/pip.
- **Native lib bundling.** Drop `.so` / `.dylib` paths in `native-libs`; they ride along in `mojo_packages/lib/`, where mojox's runtime adds them to `LD_LIBRARY_PATH`.
- **PEP 660 editable installs.** `uv pip install -e .` works. For rebuild-on-change semantics, add `cache-keys = [{ file = "pyproject.toml" }, { file = "**/*.mojo" }]` to `[tool.uv]`.
- **Reproducible builds.** ZIP and tar timestamps respect `SOURCE_DATE_EPOCH`.
- **Parallel compilation.** Multi-package repos compile their `.mojopkg` files in parallel (capped at 8 workers).
- **Preflight checks.** Missing `mojo`, missing dirs, missing native libs, dynamic-version configs → one clean error message, not a stderr dump.
- **Full PEP 621 / PEP 639 metadata.** `authors`, `maintainers`, `urls`, `keywords`, `classifiers`, `optional-dependencies`, `license-files` (copied into `.dist-info/licenses/`) all flow into the wheel METADATA.
- **`--config-setting verbose=true`.** Streams `mojo package` output during builds when you need to debug.

## How it works (architecture in five lines)

`__init__.py` re-exports the hook surface. `_hooks` is thin glue that calls into `_config` (parse pyproject), `_preflight` (environment checks), `_build` (compile + assemble), and `_metadata` (METADATA/WHEEL rendering). The whole backend has only one runtime dep beyond the stdlib: `packaging` for platform tags.

## Limitations / known gaps

- **Dynamic `project.version`** is not supported. Declare it statically.
- **Editable installs** rebuild the full wheel on every invocation; there's no source-import fast path because `.mojopkg` is compiled bytecode.
- **Cross-compilation** (`--target` per platform) is not exposed yet. Today's wheels are host-platform only.
