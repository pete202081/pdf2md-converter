"""Repository layer.

``base.py`` defines the *ports* (abstract interfaces the domain/services depend
on — Dependency Inversion); the concrete modules are SQLAlchemy *adapters*.
Tests can substitute in-memory fakes without touching infrastructure.
"""
