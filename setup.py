#!/usr/bin/env python3
"""Build the optional native (Cython) acceleration extension.

The engine runs fine as pure Python; this extension is a *speed path* only.
Build it in place with:

    pip install cython
    python setup.py build_ext --inplace        # or: make build

Then `run_sim.py --engine c` (or the default `auto`) uses it. If the extension
is not built, everything falls back to the pure-Python engine automatically.
"""
from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
except ImportError:  # pragma: no cover - only hit when Cython is absent
    cythonize = None

ext = Extension(
    "blackjack._fastsim",
    sources=["blackjack/_fastsim.pyx"],
    extra_compile_args=["-O3", "-ffast-math"],
)

if cythonize is None:
    raise SystemExit(
        "Cython is required to build the native engine: pip install cython\n"
        "(the pure-Python engine works without it)."
    )

setup(
    name="blackjack-sim-fastengine",
    py_modules=[],
    packages=[],
    ext_modules=cythonize(
        [ext],
        compiler_directives={"language_level": "3"},
        annotate=False,
    ),
    zip_safe=False,
)
