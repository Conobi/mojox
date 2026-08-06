"""Create mojo_packages/ symlinks for editable Mojo installs.

Loaded via _mojox_editable_<dist>.pth on every Python startup.
Self-contained — no imports beyond stdlib.
"""
import json
import os
import sys


def _ensure():
    """Create or update symlinks for this distribution's Mojo packages.

    Also purges symlinks in mojo_packages/ that this distribution
    previously owned but no longer declares, preventing stale links
    from accumulating across reinstalls.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    # The hook filename encodes the distribution: _mojox_editable_<dist>_hook.py
    # The manifest follows the same pattern: _mojox_editable_<dist>_manifest.json
    hook_file = os.path.basename(__file__)
    # Extract dist name: _mojox_editable_<dist>_hook.py -> <dist>
    prefix = "_mojox_editable_"
    suffix = "_hook.py"
    if hook_file.startswith(prefix) and hook_file.endswith(suffix):
        dist_name = hook_file[len(prefix):-len(suffix)]
    else:
        dist_name = ""
    manifest_name = f"_mojox_editable_{dist_name}_manifest.json" if dist_name else "_mojox_editable_manifest.json"
    manifest = os.path.join(here, manifest_name)
    if not os.path.isfile(manifest):
        return
    with open(manifest) as f:
        data = json.load(f)
    pkg_dir = os.path.join(here, "mojo_packages")
    os.makedirs(pkg_dir, exist_ok=True)

    declared = set(data.get("packages", {}).keys())

    # Create or update symlinks for declared packages
    for name, src in data.get("packages", {}).items():
        link = os.path.join(pkg_dir, name)
        if os.path.islink(link):
            if os.readlink(link) == src:
                continue
            os.unlink(link)
        elif os.path.exists(link):
            continue  # real dir/file — don't clobber
        try:
            os.symlink(src, link)
        except OSError as e:
            print(
                f"mojox-editable: failed to symlink {name} -> {src}: {e}",
                file=sys.stderr,
            )

    # Purge stale symlinks owned by this distribution
    # A previous install may have declared packages that are no longer present.
    stale_marker = os.path.join(here, f"_mojox_editable_{dist_name}_owned.json")
    previously_owned: set[str] = set()
    if os.path.isfile(stale_marker):
        with open(stale_marker) as f:
            previously_owned = set(json.load(f))

    for old_name in previously_owned - declared:
        link = os.path.join(pkg_dir, old_name)
        if os.path.islink(link):
            os.unlink(link)

    # Write the current owned set for next time
    with open(stale_marker, "w") as f:
        json.dump(sorted(declared), f)


_ensure()
