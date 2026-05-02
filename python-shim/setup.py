"""Setuptools entrypoint so ``build_native`` resolves next to this file during isolated builds."""

from __future__ import annotations

import os
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
        # from intent: non-skip builds should always produce platform-tagged wheels.
        self.root_is_pure = skip_native


setup(cmdclass={"build_py": build_py, "bdist_wheel": bdist_wheel})
