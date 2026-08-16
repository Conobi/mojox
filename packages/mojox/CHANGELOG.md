# CHANGELOG

<!-- version list -->

## v0.4.0 (2026-08-16)

### Bug Fixes

- 10 bugs in ore pipeline and planner exposed by smoke test
  ([`032f7f1`](https://github.com/Conobi/mojox/commit/032f7f140712df869538336c22e12eb02d9cc5d9))

- Add -rpath to ore pipeline link command
  ([`cb1b792`](https://github.com/Conobi/mojox/commit/cb1b792267275b4f88083179241f2951ad63103f))

- Address round 2 review findings across CLI, exec, and settings reader
  ([`3de54a0`](https://github.com/Conobi/mojox/commit/3de54a017e31e52fdb69cef01fe3eac88b6025e3))

- Close test gaps and remove dead code from review findings
  ([`ce047ba`](https://github.com/Conobi/mojox/commit/ce047ba1784cc77a00412b0b5e33244166085121))

- Correct suite:finished counts and share JSON writer
  ([`b83661e`](https://github.com/Conobi/mojox/commit/b83661e2968cf2347228402a252b41f0f3b4a3b9))

- Create output dirs, expand PATH, warn-only lints
  ([`3f625cb`](https://github.com/Conobi/mojox/commit/3f625cb7708d886729b9340872ee4f73a8c4ebd1))

- Dependency failures produce SKIPPED instead of FAIL
  ([`017cadc`](https://github.com/Conobi/mojox/commit/017cadc3a8a545dbfaea6014aef8d3c19a7f5475))

- Exclude --debug-level from ore flag forwarding
  ([`6de064f`](https://github.com/Conobi/mojox/commit/6de064f157d01ad3cccb1f1fe6f8a72bbff82866))

- Extract shared flag parser, close review gaps
  ([`e6f6643`](https://github.com/Conobi/mojox/commit/e6f66434ee90d2daf2df736e85cc2f4cfc451b1e))

- Forward all planner flags in ore pipeline step 1
  ([`5e42f5d`](https://github.com/Conobi/mojox/commit/5e42f5d9acec0dd48ace4ab7ad894a65e861796d))

- Forward optimization and debug flags in ore seed build
  ([`0487937`](https://github.com/Conobi/mojox/commit/0487937034e4eb2799af8eded2804ec9f6bea1f1))

- Harden settings reader with openat, config_paths, env threading, and walk boundaries
  ([`95a53df`](https://github.com/Conobi/mojox/commit/95a53df6b6c286c50790043f994b5345ef10c484))

- Implement filter path normalization and zero-match JSON events
  ([`a48ca8c`](https://github.com/Conobi/mojox/commit/a48ca8c388cf7bcf20df0c3b2797a08224f612e0))

- Improve CLI output ergonomics
  ([`43df27c`](https://github.com/Conobi/mojox/commit/43df27c945851a21d1e167482e0d1623216d1447))

- Include defines in ore cache key
  ([`c7bd1a3`](https://github.com/Conobi/mojox/commit/c7bd1a3c10205ced037171eae95ae068d65e6e9e))

- Include optimization flags in ore cache key
  ([`f6caf49`](https://github.com/Conobi/mojox/commit/f6caf4907b2deb9094d59d2e5cffd8ebc193b3d9))

- Inject LD_LIBRARY_PATH for native libs in standard mojo run path
  ([`32c09d7`](https://github.com/Conobi/mojox/commit/32c09d7ddd37ebe0fe341b2e505ac4ecfc5f716f))

- Ore review fixes — thread safety, define forwarding, diagnostics
  ([`9693bb6`](https://github.com/Conobi/mojox/commit/9693bb65935b45344b05fbc07a96b6f6891e8cb0))

- Resolve all mypy strict errors in mojox
  ([`803ddf2`](https://github.com/Conobi/mojox/commit/803ddf21c75b27dd419b69496fac69e8854ce746))

- Review round 1 — docstrings, source_file param, test guard, helper
  ([`5d0fac9`](https://github.com/Conobi/mojox/commit/5d0fac94be10ded90bb330c6a15fc297e07875c7))

- Review round 2 — joined -I handling, test gaps
  ([`a9c7a3e`](https://github.com/Conobi/mojox/commit/a9c7a3e188a4b1a010072e488fab9056608f76a3))

- Use project-local ore cache, fix stderr output, fix seed mismatch check
  ([`9617e1c`](https://github.com/Conobi/mojox/commit/9617e1ce03b2e74c34376e331d34095badf2d778))

- Use shared flag extractor for ore cache key computation
  ([`2d7c4e8`](https://github.com/Conobi/mojox/commit/2d7c4e88bfec7d190b4db4a135555b8fcd6ff4c6))

### Chores

- Add pytest integration marker with default deselection
  ([`a7094f4`](https://github.com/Conobi/mojox/commit/a7094f42948b88503f403566c1bc88a302b1e932))

- Add python-semantic-release config for mojox and mojox-build
  ([`db0cfa5`](https://github.com/Conobi/mojox/commit/db0cfa5ad307f58a312d6e8a06b7702c43937efb))

### Code Style

- Fix all ruff lint and format violations
  ([`027e242`](https://github.com/Conobi/mojox/commit/027e24205b253fc55a0a0bab61f5bbf2cb0bb2a6))

- Fix ruff format violations in README and _cli.py return type
  ([`b3e3451`](https://github.com/Conobi/mojox/commit/b3e345149c43b7632284efdabd912c0cabe46758))

### Documentation

- Rewrite all public READMEs for three-package architecture
  ([`31d8cba`](https://github.com/Conobi/mojox/commit/31d8cba7a69aa95865d7f052d34eb601dd874ff1))

### Features

- Add bare-assert and path-source owned lints
  ([`23e095f`](https://github.com/Conobi/mojox/commit/23e095fc290b4bf7399cecfc44610024f58b2818))

- Add concurrent command executor, fix assert(cond) lint false negative
  ([`4ca6831`](https://github.com/Conobi/mojox/commit/4ca6831ac921b25e4f947e784096710f0a79946b))

- Add determine_exit_code with test/compile failure distinction
  ([`23cfe97`](https://github.com/Conobi/mojox/commit/23cfe9762d6204fd3be144ff7f2fba413e64fc58))

- Add exec outcome types and update mojox package scaffold
  ([`fcf069c`](https://github.com/Conobi/mojox/commit/fcf069c9173e8648bc1fed72e8cf23a5667a63d7))

- Add fail-fast cancellation and on_start callback to executor
  ([`a945fca`](https://github.com/Conobi/mojox/commit/a945fca670baec75b46b85428f695a1d97d52c87))

- Add JSON diagnostic parser for Mojo compiler output
  ([`676ed9c`](https://github.com/Conobi/mojox/commit/676ed9c0dcc431a883b8f475849f6649275dec64))

- Add NDJSON event serializers and thread-safe writer
  ([`dae56c4`](https://github.com/Conobi/mojox/commit/dae56c4a44a2e7316da224a71896bcb21ad7a36c))

- Add nextest-style output verbosity modes and SKIPPED rendering
  ([`88c2fdf`](https://github.com/Conobi/mojox/commit/88c2fdfb833d474a0e5788d799dd4a0743f219bd))

- Add ore cache key computation and cache management
  ([`6fc5d73`](https://github.com/Conobi/mojox/commit/6fc5d73b702a4e0f00bed6f7816b5ceaa51bd873))

- Add ore pipeline with 6-step build-and-run
  ([`a8c0db4`](https://github.com/Conobi/mojox/commit/a8c0db4f36239247cac4a3f47b12bd00708e94f0))

- Add OreContext dataclass and LLVM tool probe
  ([`eabd242`](https://github.com/Conobi/mojox/commit/eabd2424910153636a6ece3b05516d785f4a01ab))

- Add output renderer for exec results and dry-run display
  ([`4c0be42`](https://github.com/Conobi/mojox/commit/4c0be4202d8d7030acaf07a0baf96485d238b2eb))

- Add progress output, graceful Ctrl+C, fix mojox run
  ([`a68520d`](https://github.com/Conobi/mojox/commit/a68520d9dcb6eaffe2a15196c87d97b3335c5f49))

- Add settings IO reader with secure path walk
  ([`de6386d`](https://github.com/Conobi/mojox/commit/de6386d38181ca1b87a2fe1ff82104e63241a711))

- Add single-command exec runner with timeout and signal detection
  ([`1e6f0e6`](https://github.com/Conobi/mojox/commit/1e6f0e6724a91fa7c7f577e0d5a8ebd07fbdfb82))

- Add SKIPPED outcome, OutputMode, and OutputFormat enums
  ([`bf1672b`](https://github.com/Conobi/mojox/commit/bf1672b84316378e577ee0130b9faa3c670a1508))

- Add test filtering by path prefix and name pattern
  ([`19181bd`](https://github.com/Conobi/mojox/commit/19181bd147ddf2d5319e6305b22b43b990d7a4b6))

- Add test subcommand flags for CI output, fail-fast, verbosity, and filtering
  ([`04c77b3`](https://github.com/Conobi/mojox/commit/04c77b356e7c6fecfc0308d38351751c42c5e66b))

- Rewrite check to compile packages, dedup include paths
  ([`ed19a82`](https://github.com/Conobi/mojox/commit/ed19a822de662a897a56bf4f59ccbcf599d9f203))

- Rewrite CLI with argparse subcommands and pipeline orchestration
  ([`a41b331`](https://github.com/Conobi/mojox/commit/a41b33133e28c839f872ac9ecc6176b4c32be707))

- Wire CI pipeline features into the test subcommand
  ([`450b680`](https://github.com/Conobi/mojox/commit/450b680634eca14ddc0275d5b6700a1f1da99ed7))

- Wire ore context into CLI with --no-ore flag
  ([`02e0a1d`](https://github.com/Conobi/mojox/commit/02e0a1dac12a269e68281400a77bc199101de099))

- Wire ore pipeline into exec layer
  ([`ce89c2a`](https://github.com/Conobi/mojox/commit/ce89c2a904ca0b9934bff859cc374fe5a8260007))

### Refactoring

- Extract ore pipeline to feat/ore-pipeline branch
  ([`ebde509`](https://github.com/Conobi/mojox/commit/ebde509373044ca1b38c8df1844a22971ef9c27e))

### Testing

- Add CI pipeline integration tests
  ([`1151a70`](https://github.com/Conobi/mojox/commit/1151a70b2a921a2a420cd4fa20a98ce62dfc0cae))

- Add ore activation and platform flag edge case tests
  ([`219f40e`](https://github.com/Conobi/mojox/commit/219f40e587a947ce1179be88501f1ba3270f2bad))

- Add ore pipeline regression tests for 10 smoke-test bugs
  ([`1b664fd`](https://github.com/Conobi/mojox/commit/1b664fd41392f7f1af2664aba977b856eb91821f))

- Add pipeline step failure and edge case tests
  ([`a6061db`](https://github.com/Conobi/mojox/commit/a6061db4862dca5e4756b54a9eadda7b7ae4fee3))


## v0.3.0 (2026-06-22)

### Bug Fixes

- Move sys import to top-level in hook, reuse _resolve_package_dirs in check
  ([`113ad38`](https://github.com/Conobi/mojox/commit/113ad38c2b15e652d4b8ce8a4e611d224ae773d5))

- Remove unused parameters from _write_editable_hook and _check
  ([`110a56e`](https://github.com/Conobi/mojox/commit/110a56ebc792492fb1d64705ee8c4ad2d4a50a30))

### Features

- Add mojox check subcommand for strict-mode validation
  ([`bd69042`](https://github.com/Conobi/mojox/commit/bd69042cd15d927bd9ce935cd73e5b9936a3bbeb))

- Support Mojo 1.0 precompile/.mojoc package toolchain
  ([`f0108de`](https://github.com/Conobi/mojox/commit/f0108de0539712605aa9ec646ce8733d3586fed0))


## v0.2.0 (2026-05-13)

- Initial Release
