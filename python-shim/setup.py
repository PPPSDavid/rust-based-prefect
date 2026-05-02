"""Setuptools entrypoint so ``build_native`` resolves next to this file during isolated builds."""

from __future__ import annotations

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
        native_dir = _HERE / "src" / "prefect_compat" / "native"
        has_binary = False
        if native_dir.is_dir():
            for p in native_dir.iterdir():
                if p.is_file() and p.suffix.lower() in (".so", ".dll", ".dylib"):
                    has_binary = True
                    break
        self.root_is_pure = not has_binary


setup(cmdclass={"build_py": build_py, "bdist_wheel": bdist_wheel})
