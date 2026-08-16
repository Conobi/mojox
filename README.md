# mojox

A simple utility that allows a better DX for building complete Mojo projects. Think of it a bit as the `cargo` for mojo. Three small PyPI packages give you `build`, `test`, `run`, and `check` for quickly compiling/testing.

```bash
uv init --bare hello-mojo && cd hello-mojo
uv add mojox mojo-compiler

mkdir -p src && echo 'def main(): print("hi")' > src/main.mojo
uv run mojox run src/main.mojo
```

Four commands. Pure uv. No `-I` flags, no `LD_LIBRARY_PATH` wrangling.

## Packages

| Package | PyPI | What it does |
|---|---|---|
| [`mojox`](./packages/mojox) | [![PyPI](https://img.shields.io/pypi/v/mojox)](https://pypi.org/project/mojox/) | CLI + execution layer. Subcommands: `test`, `run`, `build`, `check`, `metadata`. Reads `.mojox/config.toml` for local settings. Runs the planner's commands, handles ore acceleration in dev mode. |
| [`mojox-build`](./packages/mojox-build) | [![PyPI](https://img.shields.io/pypi/v/mojox-build)](https://pypi.org/project/mojox-build/) | The build backend. Compiles `.mojo` into `.mojoc` and packages platform-tagged wheels. |
| [`mojox-core`](./packages/mojox-core) | [![PyPI](https://img.shields.io/pypi/v/mojox-core)](https://pypi.org/project/mojox-core/) | Internal mojox features. A requirement for the other two packages to work properly. |

The Mojo compiler version is **not** pinned by any of these packages. Pin it in your own project via `mojo-compiler==X.Y.Z`. Modular's PyPI distribution handles toolchain delivery; mojox sits on top. Only Mojo 1.0+ is supported (`.mojoc` format only).

## CLI

```
mojox test       Run test targets (dev profile by default)
mojox run        Run a single .mojo file (dev profile)
mojox build      Compile binary targets (release profile)
mojox check      Validate manifest and run lints (no compiler needed)
mojox metadata   Output the build plan as JSON
```

### `mojox test`

```bash
uv run mojox test                                  # all targets, dev profile
uv run mojox test tests/unit/                      # path filter
uv run mojox test -k "parser"                      # name filter
uv run mojox test --fail-fast                      # stop on first failure
uv run mojox test --output-format json             # NDJSON event stream
uv run mojox test --success-output immediate       # show passing output live
uv run mojox test --failure-output final            # show failures after all tests
```

### `mojox run`

```bash
uv run mojox run src/main.mojo
uv run mojox run src/main.mojo --profile release
```

### `mojox build`

```bash
uv run mojox build                                 # release profile by default
uv run mojox build --profile dev --dry-run
```

Run `uv run mojox <subcommand> --help` for the full list of flags.

## Publishing a Mojo library

```toml
# my-lib/pyproject.toml
[project]
name = "boucle"
version = "0.2.0"
description = "Async event loop primitives for Mojo"
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.10"
dependencies = ["mojox", "mojo-compiler>=1.0,<2"]

[build-system]
requires      = ["mojox-build", "mojo-compiler>=1.0,<2"]
build-backend = "mojox_build"

[tool.mojox]
packages = ["boucle"]              # or `package-root = "src"` for src layout
```

```bash
uv build       # -> dist/boucle-0.2.0-py3-none-<platform>.whl
uv publish     # uploads to PyPI
```

The wheel is platform-tagged (native code) and installs `.mojoc` to `mojo_packages/`, where `mojox` discovers it automatically after a consumer runs `uv add boucle`.

## How it works (no uv plugin needed)

uv implements two PEP-standard extension points:

- **PEP 517** -- uv invokes whatever `[build-system].build-backend` declares. `mojox-build` plugs in here.
- **PEP 427 / `[project.scripts]`** -- console scripts land in `<venv>/bin/`. `mojox` is one.

uv doesn't need to know about Mojo. All Mojo-specific behavior lives in these three packages.

## License

MIT. See [`LICENSE`](./LICENSE).
