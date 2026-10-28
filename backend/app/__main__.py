"""Explicit local API launcher: ``python -m backend.app``."""

from .runtime import main

raise SystemExit(main())
