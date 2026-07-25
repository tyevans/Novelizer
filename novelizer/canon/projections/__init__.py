"""The projection registry: one module per aggregate, each registering handlers.

The handler modules are imported here purely for their import-time registration
side effects (`@projects(...)` populating HANDLERS) -- nothing in this package
references their names, hence the `noqa: F401` on each. Importing
`novelizer.canon.projections` is therefore the single act that makes the whole
dispatch table exist; a new aggregate joins it by adding a module and one import
line, with no change to the Projector.
"""
from __future__ import annotations

from novelizer.canon.projections.registry import (  # noqa: F401
    HANDLERS, ProjectionContext, projects, upsert,
)

from novelizer.canon.projections import arcs  # noqa: F401
from novelizer.canon.projections import causal  # noqa: F401
from novelizer.canon.projections import chapters  # noqa: F401
from novelizer.canon.projections import control  # noqa: F401
from novelizer.canon.projections import flags  # noqa: F401
from novelizer.canon.projections import inspiration  # noqa: F401
from novelizer.canon.projections import outline  # noqa: F401
from novelizer.canon.projections import promises  # noqa: F401
from novelizer.canon.projections import secrets  # noqa: F401
from novelizer.canon.projections import themes  # noqa: F401
from novelizer.canon.projections import threads  # noqa: F401
from novelizer.canon.projections import world  # noqa: F401

__all__ = ["HANDLERS", "ProjectionContext", "projects", "upsert"]
