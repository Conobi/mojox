# mojox

A drop-in wrapper around the [Mojo](https://docs.modular.com/mojo/) CLI that automatically discovers and wires installed Mojo packages.

## The problem

Mojo has no `MOJO_IMPORT_PATH`. Every invocation needs explicit `-I` flags and `LD_LIBRARY_PATH` for installed packages:

```bash
MOJO_PKG=$(python -c "import sysconfig; print(sysconfig.get_path('platlib') + '/mojo_packages')")
LD_LIBRARY_PATH="$MOJO_PKG/lib" mojo run -I "$MOJO_PKG" my_app.mojo
```

## The fix

```bash
mojox run my_app.mojo
```

`mojox` does three things before delegating to `mojo`:

1. Finds `site-packages/mojo_packages/` via `sysconfig`
2. Injects `-I` so the compiler sees installed `.mojopkg` files
3. Sets `LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH` for native libraries

Everything else passes through unchanged. `mojox run`, `mojox build`, `mojox package` — same flags, same behavior.

## Install

```bash
uv add mojox        # in a project
uv tool install mojox   # globally
```

`mojox` depends on `mojo-compiler`, which is installed automatically.

## Usage

### As a consumer

```bash
# Install a Mojo library
uv add mojo-foo

# Run code that imports from it — no -I needed
uv run -- mojox run my_app.mojo
uv run -- mojox build my_app.mojo
```

### As a library author

```bash
# Your pyproject.toml
# [dependency-groups]
# dev = ["mojox", "mojo-io-uring>=0.1.0"]

uv sync

# mojox finds installed deps; -I . adds your local source
uv run -- mojox run -I . -D ASSERT=all tests/test_foo.mojo
uv run -- mojox package my_lib -I . -o my_lib.mojopkg
```

### Bare mojo still works

`mojox` is additive. The `mojo` command is still available in the same venv:

```bash
uv run -- mojo run -I /some/path my_file.mojo   # manual control
uv run -- mojox run my_file.mojo                 # automatic discovery
```

## How it works

`mojox` is ~15 lines of Python. On every invocation it:

```
sys.argv:  mojox run my_app.mojo
                 ↓
inject:    mojo run -I<platlib>/mojo_packages my_app.mojo
                 ↓
env:       LD_LIBRARY_PATH=<platlib>/mojo_packages/lib:$LD_LIBRARY_PATH
                 ↓
exec:      os.execve(mojo_binary, ...)
```

The `-I` is only injected for subcommands that accept it (`run`, `build`, `package`, `test`, `repl`, `doc`, `format`, `debug`). Top-level flags like `mojox --version` pass through untouched.

## The convention

Mojo packages distributed via PyPI install their artifacts to a shared directory:

```
site-packages/
  mojo_packages/          # .mojopkg files (via wheel .data/platlib/)
    foo.mojopkg
    io_uring.mojopkg
    lib/
      libfoo.so         # native shared libraries
```

One `-I`, one `LD_LIBRARY_PATH` covers the entire dependency tree. See [mojo-foo](https://github.com/Conobi/mojo-foo) for a real-world example.

## License

MIT
