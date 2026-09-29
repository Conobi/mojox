# Releasing

This repo publishes three packages independently:

- `mojox` → automated via python-semantic-release (PSR)
- `mojox-build` → automated via python-semantic-release (PSR)
- `mojox-core` → manual version bump + tag push

Every release goes through a **draft GitHub Release** first. Nothing is
uploaded to PyPI until a maintainer reviews the notes and publishes the draft.
There is no TestPyPI step — CI (ruff + mypy + pytest + build matrix) validates
the code before release.

## mojox & mojox-build (PSR-powered)

Version bumps are determined automatically from conventional commit messages:

- `fix:` → patch bump (0.5.0 → 0.5.1)
- `feat:` → minor bump (0.5.0 → 0.6.0)
- `feat!:` or `BREAKING CHANGE:` footer → major bump (0.5.0 → 1.0.0)
- `chore:`, `refactor:`, `docs:`, `test:`, `ci:` → no bump

1. Go to **Actions → Release → Run workflow**
2. Select the package (`mojox` or `mojox-build`) and run it

PSR bumps `version` in `pyproject.toml`, runs `uv lock`, then commits
(including `uv.lock`), tags and pushes. The workflow then creates a draft
release for the tag. If no bump-worthy commits exist, it exits cleanly.

## mojox-core (manual tag push, also a fallback for the others)

```bash
# 1. Bump version in pyproject.toml
$EDITOR packages/<package>/pyproject.toml

# 2. Refresh the lock so it records the new version
uv lock

# 3. Commit + tag + push
git commit -m "chore: release <package> <version>" -- packages/<package>/pyproject.toml uv.lock
git tag <package>-v<version>
git push origin main --tags
```

The workflow verifies the tag against `pyproject.toml` and creates a draft
release. Re-running it when a release already exists for the tag is a no-op.

## Review and publish

1. Open **Releases** on GitHub: the draft (`<package> v<version>`, notes
   "Release notes pending review.") is listed only to users with write access.
2. Edit the notes (**Generate release notes** helps as a starting point).
3. Publish it from the web UI, or with
   `gh release edit <package>-v<version> --draft=false`.

Publishing fires the `release: published` event, and the `publish-pypi` job
builds that tag and uploads it to PyPI via trusted publishing (OIDC). The
draft must be published from your own account: events caused by the
workflow's `GITHUB_TOKEN` do not start workflow runs.

- **PyPI job failed?** Fix the cause and use **Re-run jobs** on that run in
  Actions. Re-running reuses the original event, so it builds the same tag.
- **Marked as pre-release?** The job refuses GitHub pre-releases. Untick
  "pre-release", set the release back to draft, and publish it again.
  PEP 440 pre-release versions (e.g. `1.0.0rc1`) are fine as regular releases.

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
