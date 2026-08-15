# mojox

CLI and execution layer for Mojo build tooling.

`mojox` reads your `pyproject.toml` manifest (via `mojox-core`), discovers targets, builds an execution plan, and runs `mojo` commands with structured output. It handles profile resolution, local settings (`.mojox/config.toml`), diagnostic formatting, and -- in dev mode -- transparent ore acceleration (LLVM bitcode splitting for faster rebuilds) when LLVM tools are available.

## Install

```bash
uv add mojox "mojo-compiler==1.0.0"    # in a project
uv tool install mojox                      # globally
```

## Subcommands

### `test`

Run test targets (dev profile by default).

```bash
mojox test                                    # all test targets
mojox test -k "parse" tests/unit/             # filter by name + path
mojox test --output-format json               # NDJSON to stdout (CI)
mojox test --no-fail-fast                     # run all tests even after failures
mojox test --failure-output final             # show failing output after all tests
```

Test-specific flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--output-format {human,json}` | `human` | Output format; `json` emits NDJSON to stdout |
| `--fail-fast` / `--no-fail-fast` | `--fail-fast` | Stop after first failure |
| `--success-output {immediate,final,never}` | `never` | When to show passing test output |
| `--failure-output {immediate,final,never}` | `immediate` | When to show failing test output |
| `-k` / `--filter PATTERN` | | Filter tests by name (case-insensitive substring) |
| `paths` (positional) | | Filter tests by file or directory prefix |

### `run`

Run a single `.mojo` file (dev profile by default).

```bash
mojox run src/main.mojo
```

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

## Common flags

These flags are available on every subcommand:

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
6. In dev mode, transparently uses ore acceleration when LLVM tools are available; degrades gracefully to standard `mojo run`

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Test failure(s) |
| `2` | Compilation or configuration error |

## See also

- [`mojox-core`](../mojox-core/) -- manifest parsing, target discovery, and build planning (pure model layer)
- [`mojox-build`](../mojox-build/) -- PEP 517 build backend for packaging Mojo libraries as wheels

## License

MIT
