"""
高性能UDP接收器 - setup.py
用于编译Cython扩展模块
"""

from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

extensions = [
    Extension(
        "fast_udp_receiver",
        ["fast_udp_receiver.pyx"],
        include_dirs=[np.get_include()],
        extra_compile_args=(
            ["/O2"] if __import__("sys").platform == "win32" else ["-O3"]
        ),
        language="c",
    )
]

setup(
    name="fast_udp_receiver",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "embedsignature": True,
        },
    ),
    zip_safe=False,
)
