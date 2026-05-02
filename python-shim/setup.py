"""Setuptools entrypoint so ``build_native`` resolves next to this file during isolated builds."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from build_native import build_py


class bdist_wheel(_bdist_wheel):
    """Tag wheels as platform-specific when a ctypes cdylib was staged under ``native/``."""

    def finalize_options(self) -> None:
        super().finalize_options()
        skip_native = os.environ.get("IRONFLOW_SKIP_NATIVE_BUILD", "").lower() in ("1", "true", "yes")
        # build_py stages the cdylib later in the build lifecycle, so decide purity
        # from intent. Keep Linux pure for auditwheel repair to attach manylinux tags,
        # but force platform tags for Windows/macOS when native build is enabled.
        if skip_native:
            self.root_is_pure = True
            return
        self.root_is_pure = platform.system() == "Linux"


setup(cmdclass={"build_py": build_py, "bdist_wheel": bdist_wheel})
