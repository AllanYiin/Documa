"""Prefer Documa's private LingXi, without owning the top-level lingxi package."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from types import ModuleType


BUNDLED_LINGXI_VERSION = "0.4.5"
_BUNDLED_MODULE = "documa._vendor.lingxi"


def lingxi_binding() -> tuple[ModuleType, str]:
    """Resolve bundled identity first; retain external source-checkout support.

    Only an absent extension permits the legacy external distribution fallback.
    A broken DLL, missing model or unexpected bundled version must not silently
    switch providers. Wheels always ship the private extension and model files.
    """
    try:
        module = import_module(_BUNDLED_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name not in {_BUNDLED_MODULE, f"{_BUNDLED_MODULE}._core"}:
            raise
        try:
            version = distribution_version("lingxi")
        except PackageNotFoundError as missing:
            raise ImportError("Bundled LingXi is unavailable; build/reinstall the Documa wheel.") from missing
        return import_module("lingxi"), version
    version = getattr(module, "__version__", None)
    if version != BUNDLED_LINGXI_VERSION:
        raise ImportError(f"Bundled LingXi {BUNDLED_LINGXI_VERSION} required; found {version!r}")
    return module, version


def load_segmenter(module: ModuleType):
    if module.__name__ == _BUNDLED_MODULE:
        # Do not let an unrelated global LINGXI_ASSETS override the pinned model.
        return module.load(asset_dir=Path(module.__file__).parent / "assets")
    return module.load()
