"""Setuptools entrypoint so ``build_native`` resolves next to this file during isolated builds."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from build_native import build_py  # noqa: E402


class BinaryDistribution(Distribution):
    """Treat native-enabled builds as platform wheels (platlib), not purelib."""

    def has_ext_modules(self) -> bool:
        skip_native = os.environ.get("FLOWOXIDE_SKIP_NATIVE_BUILD", "").lower() in (
            "1",
            "true",
            "yes",
        )
        return not skip_native


class bdist_wheel(_bdist_wheel):
    """Tag wheels as platform-specific when a ctypes cdylib was staged under ``native/``."""

    def finalize_options(self) -> None:
        super().finalize_options()
        skip_native = os.environ.get("FLOWOXIDE_SKIP_NATIVE_BUILD", "").lower() in (
            "1",
            "true",
            "yes",
        )
        force_platform = os.environ.get(
            "FLOWOXIDE_FORCE_PLATFORM_WHEEL", ""
        ).lower() in ("1", "true", "yes")
        # build_py stages the cdylib later in the build lifecycle, so decide purity
        # from intent. Keep Linux pure for auditwheel repair to attach manylinux tags,
        # but force platform tags for Windows/macOS when native build is enabled.
        if skip_native:
            self.root_is_pure = True
            return
        if force_platform:
            self.root_is_pure = False
            return
        self.root_is_pure = platform.system() == "Linux"


setup(
    distclass=BinaryDistribution,
    cmdclass={"build_py": build_py, "bdist_wheel": bdist_wheel},
)
