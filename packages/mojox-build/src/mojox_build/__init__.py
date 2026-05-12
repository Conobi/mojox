"""mojox-build: PEP 517 + PEP 660 build backend for Mojo libraries.

PEP 517 frontends call these top-level names; the actual implementations live
in `_hooks` so this file can stay a flat re-export surface.
"""

from ._build import GENERATOR_VERSION as __version__
from ._hooks import (
    hook_build_editable as build_editable,
    hook_build_sdist as build_sdist,
    hook_build_wheel as build_wheel,
    hook_get_requires_for_build_editable as get_requires_for_build_editable,
    hook_get_requires_for_build_sdist as get_requires_for_build_sdist,
    hook_get_requires_for_build_wheel as get_requires_for_build_wheel,
    hook_prepare_metadata_for_build_editable as prepare_metadata_for_build_editable,
    hook_prepare_metadata_for_build_wheel as prepare_metadata_for_build_wheel,
)

__all__ = [
    "__version__",
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_editable",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_editable",
    "prepare_metadata_for_build_wheel",
]
