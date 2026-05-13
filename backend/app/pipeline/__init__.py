# app/pipeline/__init__.py
"""
Review pipeline package.

Public surface — import from here, not from submodules directly.
"""
from app.pipeline.orchestrator import async_pipeline

__all__ = ["async_pipeline"]
