"""Shim so that older pip/setuptools can `pip install -e .`; metadata lives in pyproject.toml."""
from setuptools import setup

setup()
