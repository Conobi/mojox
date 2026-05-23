"""Create mojo_packages/ symlinks for editable Mojo installs.

Loaded via _mojox_editable.pth on every Python startup.
Self-contained — no imports beyond stdlib.
"""
import json
import os


def _ensure():
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = os.path.join(here, "_mojox_editable_manifest.json")
    if not os.path.isfile(manifest):
        return
    with open(manifest) as f:
        data = json.load(f)
    pkg_dir = os.path.join(here, "mojo_packages")
    os.makedirs(pkg_dir, exist_ok=True)
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
            import sys
            print(
                f"mojox-editable: failed to symlink {name} -> {src}: {e}",
                file=sys.stderr,
            )


_ensure()
