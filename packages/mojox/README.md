# mojox

CLI wrapper for Mojo with automatic package discovery.

`mojox` is ~15 lines of Python. On every invocation it:

1. Finds `<site-packages>/mojo_packages/` via `sysconfig`.
2. Injects `-I` so `mojo` sees installed `.mojoc` / `.mojopkg` files.
3. Augments `LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH` for native libs in `<pkg>/lib`.
4. Hands off to `mojo._entrypoints.exec_mojo` (the same entry point the bundled `mojo` binary uses).

## Install

```bash
uv add mojox                # in a project
uv tool install mojox       # globally
```

`mojox` depends on `mojo-compiler`, which is installed automatically.

## Usage

```bash
uv run mojox run my_app.mojo
uv run mojox build my_app.mojo
uv run mojox test tests/
uv run mojox precompile my_lib -o my_lib.mojoc   # `package … -o …mojopkg` on Mojo < 1.0
```

Subcommands that get `-I` injected: `run`, `build`, `precompile`, `package`, `test`, `repl`, `doc`, `format`, `debug`.

Top-level flags (`mojox --version`, etc.) pass through untouched.

## See also

- [`mojox-build`](../mojox-build/) — PEP 517 build backend so Mojo libraries can be packaged as wheels and consumed via `uv add`.
