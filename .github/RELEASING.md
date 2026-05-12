# Releasing

This repo publishes two packages independently:

- `mojox` → tag `mojox-vX.Y.Z`
- `mojox-build` → tag `mojox-build-vX.Y.Z`

## One-time setup per package

PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/) is configured per package. For each package, do this once:

### If the package already exists on PyPI (case: `mojox`)

1. <https://pypi.org/manage/project/mojox/settings/publishing/>
2. **Add a new publisher** with:
   - Owner: `Conobi`
   - Repository name: `mojox`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

### If the package is brand new (case: `mojox-build`)

Use a **pending publisher** so the first OIDC publish *creates* the project on PyPI:

1. <https://pypi.org/manage/account/publishing/> (logged in to your account)
2. **Add a new pending publisher** with:
   - Project name: `mojox-build`
   - Owner: `Conobi`
   - Repository name: `mojox`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

The pending publisher becomes a real publisher automatically the first time the workflow publishes successfully.

### TestPyPI (optional, for dry runs)

Repeat the appropriate flow on <https://test.pypi.org/manage/account/publishing/> with environment name `testpypi`. Use the workflow's manual dispatch to publish there.

## GitHub environments

The workflow's `environment: pypi` / `environment: testpypi` is what PyPI's publisher rule matches against. Create those two environments in **Settings → Environments** on the repo (no special config needed, just the names).

## Releasing a version

```bash
# 1. Bump version in the relevant package
$EDITOR packages/mojox-build/pyproject.toml      # version = "0.2.1"

# 2. Commit
git commit -am "chore(mojox-build): bump to 0.2.1" -- packages/mojox-build/pyproject.toml

# 3. Tag
git tag mojox-build-v0.2.1

# 4. Push
git push origin main --tags
```

The release workflow:
1. Verifies tag version matches `pyproject.toml` (fails fast otherwise).
2. `uv build --package <pkg>`.
3. `uv publish --trusted-publishing always` exchanges the GitHub OIDC token for an ephemeral PyPI credential. No tokens stored.

## TestPyPI dry-run

For changes you want to validate before they touch real PyPI:

1. **Actions → Release → Run workflow**.
2. Pick the package and `testpypi` as the destination.
3. Install into a throwaway venv to verify:
   ```bash
   uv venv /tmp/dry && cd /tmp/dry
   uv pip install \
     --index-strategy unsafe-best-match \
     --index https://test.pypi.org/simple/ \
     --index https://pypi.org/simple/ \
     mojox-build==0.2.0 mojo-compiler==0.26.2
   ```

`--index-strategy unsafe-best-match` lets uv fall back to real PyPI for build dependencies that TestPyPI doesn't mirror (e.g. `packaging`).
