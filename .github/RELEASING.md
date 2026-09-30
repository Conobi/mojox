# Releasing mojox, mojox-core and mojox-build

Each package is released on its own, in two phases: you **create a draft release**, then you **publish the draft**. Publishing the draft uploads the package to PyPI, so nothing goes public until you've reviewed the release notes.

> ⚠️ **Publish drafts from your own GitHub account.** A draft published by a workflow doesn't trigger the PyPI upload.
>
> ⚠️ **Release mojox-core first** when a change spans mojox-core and another package.

---

## One-time setup

### What You'll Need
- Maintainer access to the `Conobi/mojox` GitHub repository
- Owner access to the three projects on PyPI

### Steps

1. **Create the GitHub environment**
   - Go to **Settings → Environments** on the repository
   - Create an environment named `pypi` (no other configuration needed)

2. **Add a trusted publisher for each package**
   - Open the package's PyPI publishing settings:
     - `mojox`: <https://pypi.org/manage/project/mojox/settings/publishing/>
     - `mojox-build`: <https://pypi.org/manage/project/mojox-build/settings/publishing/>
     - `mojox-core`: <https://pypi.org/manage/project/mojox-core/settings/publishing/>
   - Click **Add a new publisher** and enter:
     - Owner: `Conobi`
     - Repository name: `mojox`
     - Workflow name: `release.yml`
     - Environment name: `pypi`
   - For a brand-new package, add a **pending publisher** at <https://pypi.org/manage/account/publishing/> instead

> ✅ No API token is stored anywhere: the workflow exchanges its GitHub OIDC token for a short-lived PyPI credential ([trusted publishing](https://docs.pypi.org/trusted-publishers/)).

---

## Release mojox or mojox-build

The version is computed from the commit messages since the last release (see [Version bumps](#version-bumps)).

1. **Start the release**
   - Go to **Actions → Release → Run workflow**
   - Select `mojox` or `mojox-build`
   - Click **Run workflow**

2. **Check the result**
   - A commit `chore: release <package> <version>` appears on `main`, with the new version and the updated `uv.lock`
   - A tag `<package>-v<version>` and a draft release appear
   - If no commit calls for a new version, the workflow ends without releasing

3. **Publish the draft** (see [Publish the draft](#publish-the-draft))

---

## Release mojox-core

mojox-core is released by pushing a tag. This also works for mojox and mojox-build if the workflow above is unavailable.

1. **Bump the version**
   - Edit `version` in `packages/<package>/pyproject.toml`
   - Run `uv lock` so the lockfile records the new version

2. **Commit, tag and push**
   ```bash
   git commit -m "chore: release <package> <version>" -- packages/<package>/pyproject.toml uv.lock
   git tag <package>-v<version>
   git push origin main <package>-v<version>
   ```

3. **Check the result**
   - The Release workflow checks that the tag matches `pyproject.toml`
   - A draft release appears for the tag
   - Running it again for the same tag does nothing

4. **Publish the draft** (see below)

---

## Publish the draft

1. **Open the draft**
   - Go to **Releases** on GitHub
   - Find the draft `<package> v<version>` with the text "Release notes pending review."
   - Only users with write access can see drafts

2. **Write the release notes**
   - Replace the placeholder text with the approved notes
   - **Generate release notes** gives you a list of commits to start from

3. **Publish it**
   - Click **Publish release**, or run `gh release edit <package>-v<version> --draft=false`
   - The **Publish to PyPI** job starts in **Actions**, builds the tagged version and uploads it

4. **Check PyPI**
   - The new version appears on the package's PyPI page within a few minutes

---

## When a change spans mojox-core and another package

1. Release mojox-core and publish its draft
2. Check that the new mojox-core version is on PyPI
3. Release the other package

---

## If Something Fails

- **The PyPI job failed**: fix the cause, then click **Re-run jobs** on that run in **Actions**. It rebuilds the same tag.
- **The release was marked as a pre-release**: the PyPI job refuses pre-releases. Untick **Set as a pre-release**, set the release back to draft, and publish it again. Pre-release version numbers such as `1.0.0rc1` are fine as regular releases.
- **Nothing reached PyPI after publishing**: check that you published the draft from your own account, not from a workflow.

---

## Version bumps

For mojox and mojox-build, the commit types since the last release decide the new version:

| Commit type | Bump | Example |
|---|---|---|
| `fix:` | patch | 0.5.0 → 0.5.1 |
| `feat:` | minor | 0.5.0 → 0.6.0 |
| `feat!:` or a `BREAKING CHANGE:` footer | major | 0.5.0 → 1.0.0 |
| `chore:`, `refactor:`, `docs:`, `test:`, `ci:` | none | — |

There is no TestPyPI step: CI (ruff, mypy, pytest and the build matrix) checks the code before any release.
