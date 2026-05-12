# Releasing

This repo publishes two packages independently:

- `mojox` → tag `mojox-vX.Y.Z`
- `mojox-build` → tag `mojox-build-vX.Y.Z`

## One-time setup per package

PyPI trusted publishing is configured per package, not per repo. For each of the two packages, do this once:

1. **Create the project** on PyPI (or TestPyPI) — for a brand-new name, you can't pre-register a trusted publisher without owning the project. The first release has to be authenticated by hand (`uv publish --token …`), after which step 2 takes over for all subsequent releases.
2. **PyPI → Manage → Publishing → Add a new pending publisher** with:
   - Owner: `Conobi`
   - Repository: `mojox`
   - Workflow filename: `release.yml`
   - Environment name: `pypi` (or `testpypi` for the test index)

Repeat for TestPyPI separately if you want a dry-run target.

## Releasing a version

1. Bump the version in the relevant `packages/<pkg>/pyproject.toml`.
2. Commit the bump: `chore(<pkg>): bump to X.Y.Z`.
3. Tag the commit: `git tag mojox-vX.Y.Z` (or `mojox-build-vX.Y.Z`).
4. Push: `git push origin main --tags`.
5. Watch the **Release** workflow run. It will:
   - Verify the tag's version matches the pyproject.toml version (fail fast if not).
   - `uv build --package <pkg>` produces `dist/<pkg>-X.Y.Z-…`.
   - `uv publish --trusted-publishing always` exchanges the GitHub OIDC token for an ephemeral PyPI credential — no secrets stored.

## TestPyPI dry-run

For changes you want to validate before they touch real PyPI:

1. **Actions → Release → Run workflow**.
2. Pick the package and select `testpypi` as the destination.
3. After it publishes, install in a throwaway venv:
   ```bash
   uv venv /tmp/dry && cd /tmp/dry
   uv pip install \
     --index-strategy unsafe-best-match \
     --index https://test.pypi.org/simple/ \
     --index https://pypi.org/simple/ \
     mojox-build==X.Y.Z mojo-compiler==0.26.2
   ```

The `--index-strategy unsafe-best-match` is needed because TestPyPI doesn't mirror PyPI, so build dependencies (`packaging`, `mojo-compiler`) resolve from real PyPI.
