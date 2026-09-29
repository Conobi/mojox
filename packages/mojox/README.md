# mojox

CLI and execution layer for Mojo build tooling.

`mojox` reads your `pyproject.toml` manifest (via `mojox-core`), discovers targets, builds an execution plan, and runs `mojo` commands with structured output. It handles profile resolution, local settings (`.mojox/config.toml`), diagnostic formatting, and a compiled-binary cache so unchanged tests and programs are not rebuilt.

## Install

```bash
uv add mojox "mojo-compiler==1.0.0"    # in a project
uv tool install mojox                      # globally
```

## Subcommands

### `test`

Run test targets (dev profile by default). Each test file is built to a binary, then executed.

```bash
mojox test                                    # all test targets
mojox test -k "parse" tests/unit/             # filter by name + path
mojox test --output-format json               # NDJSON to stdout (CI)
mojox test --no-fail-fast                     # run all tests even after failures
mojox test --failure-output final             # show failing output after all tests
mojox test --bundle -k "parse"                # one harness binary, filter test functions
mojox test --examples                         # also compile-check examples/*/main.mojo
```

Test-specific flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--output-format {human,json}` | `human` | Output format; `json` emits NDJSON to stdout |
| `--fail-fast` / `--no-fail-fast` | `--fail-fast` | Stop after first failure |
| `--success-output {immediate,final,never}` | `never` | When to show passing test output |
| `--failure-output {immediate,final,never}` | `immediate` | When to show failing test output |
| `-k` / `--filter PATTERN` | | Filter tests by name (case-insensitive substring); see `--bundle` |
| `--bundle` | off | Compile every `test_*` function into a single harness binary |
| `--examples` | off | Also compile-check `examples/*/main.mojo` |
| `--no-cache` | off | Always rebuild; the existing cache is left untouched |
| `paths` (positional) | | Filter tests by file or directory prefix |

Examples are not checked unless you pass `--examples` or a path under `examples/`. They are compiled, never executed.

With `--bundle`, test files without `test_*` functions are left out of the bundle (with a note on stderr), and `-k` matches test function names inside the harness at runtime. Without it, `-k` matches test file paths.

#### Binary cache

Build outputs live under `.mojox/cache/`: test binaries in `bin/`, their cache keys in `meta/`, example builds in `examples/`. A test is rebuilt only when its key changes. The key covers the test source, the project's library and test trees, the dependency include dirs (file size and mtime, not content), the compiler flags, the build environment and the compiler. `mojox cache clean` deletes `.mojox/cache/`.

Add `.mojox/` to your `.gitignore`.

### `run`

Build a single `.mojo` file, then execute the binary (dev profile by default).

```bash
mojox run src/main.mojo
mojox run --no-cache src/main.mojo            # always rebuild
```

The binary goes through the same `.mojox/cache/bin` cache as `mojox test`, so a second run of an unchanged file skips the build. Editing the file, a sibling module or a dependency triggers a rebuild. Output is captured and replayed once the program exits, not streamed. `--timeout` (or the profile's timeout) covers the build and the run.

Exit code: the program's own code; `128 + N` if it is killed by signal `N`; `1` if the build fails (compiler crash included) or the timeout is hit.

### `build`

Compile binary targets (release profile by default).

```bash
mojox build
```

### `check`

Validate the manifest and run lints. No compiler needed.

```bash
mojox check
```

### `metadata`

Output the build plan as JSON.

```bash
mojox metadata
```

### `cache clean`

Delete `.mojox/cache/` (cached binaries and their metadata). Safe to run when there is no cache.

```bash
mojox cache clean
```

## Common flags

These flags are available on every subcommand except `cache`:

| Flag | Description |
|------|-------------|
| `--profile PROFILE` | Build profile (`dev`, `release`, or user-defined) |
| `-j` / `--jobs N` | Maximum concurrent compilations |
| `--timeout SECONDS` | Per-target timeout |
| `--dry-run` | Show planned commands without executing |
| `-v` / `--verbose` | Expand grouped output |
| `--no-config` | Disable `.mojox/config.toml` discovery |
| `--config-file PATH` | Explicit config file path |
| `-D` / `--define KEY=VALUE` | Define a compile-time variable (repeatable) |
| `--flag VALUE` | Extra flag passed through to the compiler (repeatable; use `--flag=VALUE` for dash-prefixed flags) |

## How it works

1. Parses `pyproject.toml` via `mojox-core` (manifest model layer)
2. Reads `.mojox/config.toml` local settings, if present (TOCTOU-safe via `openat`)
3. Resolves policy: profile, defines, flags, CLI overrides
4. Discovers targets and builds a plan (pure -- returns commands as data)
5. Executes the plan: runs `mojo` commands, collects outcomes, formats output
6. For tests and `run`, builds each file to a binary (reusing a cached one when its key matches), then executes it

## Exit codes

`mojox test`:

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Test failure(s), including a test file that fails to build |
| `2` | Library or example compilation failure, or configuration error |
| `130` | Interrupted (Ctrl+C) |

`mojox run`:

| Code | Meaning |
|------|---------|
| any | The program's own exit code |
| `128 + N` | The program was killed by signal `N` |
| `1` | Build failure or timeout |
| `2` | Toolchain not found |

## See also

- [`mojox-core`](../mojox-core/) -- manifest parsing, target discovery, and build planning (pure model layer)
- [`mojox-build`](../mojox-build/) -- PEP 517 build backend for packaging Mojo libraries as wheels

## License

MIT
