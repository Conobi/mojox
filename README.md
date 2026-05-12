# mojox

A pure-uv DX for Mojo — runtime + build backend, two tiny PyPI packages, no fork of uv, no plugin.

| Package | Role | Source |
|---|---|---|
| [`mojox`](./packages/mojox) | Runtime shim. Auto-injects `-I site-packages/mojo_packages/` and `LD_LIBRARY_PATH` so Mojo finds installed `.mojopkg` files. | ~30 lines |
| [`mojox-build`](./packages/mojox-build) | PEP 517 / PEP 660 build backend. Compiles `.mojo` → `.mojopkg` and assembles them into platform-tagged wheels. | ~600 lines |

The Mojo version is **not** pinned by either package — pin it in your own project via `mojo-compiler==X.Y.Z`. Modular's PyPI distribution does the toolchain delivery; we just sit on top of it.

## End-user DX (greenfield Mojo app)

```bash
uv init --bare hello-mojo && cd hello-mojo
uv add mojox "mojo-compiler==0.26.2"
uv add "boucle @ git+https://github.com/Conobi/boucle@v0.2.0"

mkdir -p src && echo 'fn main(): print("hi")' > src/main.mojo
uv run mojox run src/main.mojo
```

Five commands, pure uv. `mojox` finds installed Mojo packages automatically — no `-I` flags, no `LD_LIBRARY_PATH` handling.

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
dependencies = ["mojox", "mojo-compiler>=0.26,<0.27"]

[build-system]
requires      = ["mojox-build>=0.2", "mojo-compiler>=0.26,<0.27"]
build-backend = "mojox_build"

[tool.mojox-build]
packages = ["boucle"]              # or `package-root = "src"` for src layout
```

```bash
uv build       # → dist/boucle-0.2.0-py3-none-manylinux_2_43_x86_64.whl
uv publish     # uploads to PyPI
```

The wheel is correctly **platform-tagged** (because `.mojopkg` is compiled native code) and lays the `.mojopkg` at `mojo_packages/boucle.mojopkg`, where `mojox` discovers it after a consumer does `uv add boucle`.

## Why this works without uv plugins

uv has two PEP-standard extension points it implements faithfully:

- **PEP 517** — uv invokes whatever `[build-system].build-backend` declares. `mojox-build` plugs in here.
- **PEP 427 / `[project.scripts]`** — console scripts land in `<venv>/bin/`. `mojox` is one of those.

uv doesn't need to know about Mojo — it just installs wheels and runs scripts. All Mojo-specific behavior lives in our two packages.

## Repo layout

This repository is a uv workspace:

```
.
├── pyproject.toml                ← workspace root
├── packages/
│   ├── mojox/                    ← runtime shim package
│   │   ├── pyproject.toml
│   │   └── src/mojox/
│   └── mojox-build/              ← PEP 517/660 backend
│       ├── pyproject.toml
│       └── src/mojox_build/
└── README.md (this file)
```

Each package versions and releases independently. Develop with `uv sync` at the workspace root; the workspace lockfile keeps both in sync.

## License

MIT. See [`LICENSE`](./LICENSE).
