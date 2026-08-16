# Releasing

This repo publishes three packages independently:

- `mojox` → automated via python-semantic-release (PSR)
- `mojox-build` → automated via python-semantic-release (PSR)
- `mojox-core` → manual version bump + tag push

All releases publish directly to PyPI. There is no TestPyPI step — CI
(ruff + mypy + pytest + build matrix) validates the code before release.

## mojox & mojox-build (PSR-powered)

Version bumps are determined automatically from conventional commit messages:

- `fix:` → patch bump (0.5.0 → 0.5.1)
- `feat:` → minor bump (0.5.0 → 0.6.0)
- `feat!:` or `BREAKING CHANGE:` footer → major bump (0.5.0 → 1.0.0)
- `chore:`, `refactor:`, `docs:`, `test:`, `ci:` → no bump

### To release

1. Go to **Actions → Release → Run workflow**
2. Select the package (`mojox` or `mojox-build`)
3. Click **Run workflow**

PSR will:
1. Analyze commits since the last tag for that package
2. Determine the bump level from commit messages
3. Update `version` in `pyproject.toml`
4. Generate/update `CHANGELOG.md` in the package directory
5. Commit, tag, and push
6. Create a GitHub Release
7. Build and publish to PyPI via trusted publishing (OIDC)

If no bump-worthy commits exist, the workflow exits cleanly without releasing.

## Manual release (any package)

Any package can be released manually via tag push. This is the primary
method for mojox-core, and a fallback for mojox and mojox-build.

```bash
# 1. Bump version in pyproject.toml
$EDITOR packages/<package>/pyproject.toml

# 2. Commit + tag + push
git commit -am "chore: release <package> <version>" -- packages/<package>/pyproject.toml
git tag <package>-v<version>
git push origin main --tags
```

The release workflow automatically:
1. Verifies the tag version matches `pyproject.toml`
2. Builds with `uv build --package <package>`
3. Publishes to PyPI via `uv publish --trusted-publishing always`
4. Creates a GitHub Release

## Release ordering

When a change spans mojox-core and a dependent package:
1. Release mojox-core first (manual tag-push)
2. Verify it's available on PyPI
3. Then trigger the dependent's release via workflow dispatch

## Trusted publishing setup

PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/) is configured per package.
No API tokens are stored — the workflow exchanges its GitHub OIDC token for an ephemeral PyPI credential.

### For each package

1. Go to the package's PyPI publishing settings:
   - `mojox`: <https://pypi.org/manage/project/mojox/settings/publishing/>
   - `mojox-build`: <https://pypi.org/manage/project/mojox-build/settings/publishing/>
   - `mojox-core`: <https://pypi.org/manage/project/mojox-core/settings/publishing/>
2. **Add a new publisher** with:
   - Owner: `Conobi`
   - Repository name: `mojox`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

For a brand-new package, use a **pending publisher** at <https://pypi.org/manage/account/publishing/>.

## GitHub environments

Create a `pypi` environment in **Settings → Environments** on the repo
(no special config needed, just the name).
