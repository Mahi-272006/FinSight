"""API router package for FinSight.

Each sub-module contains one APIRouter.  main.py imports and mounts them all
under /api/v1.  This __init__.py stays intentionally thin -- no re-exports
that would couple the package import to heavy services.
"""
