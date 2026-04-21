"""MGP sidecar package for nanobot.

This subpackage is **opt-in**: it is only imported when ``mgp.enabled = true``
in the agent config. Importing this top-level module is cheap and does NOT
trigger an ``import mgp_client`` — the optional dependency is checked lazily
in :func:`build_sidecar`.

Public surface (intentionally tiny):

* :func:`build_sidecar` — factory that returns an ``AsyncMGPSidecar``,
  raising a clear install hint when ``mgp-client`` is missing.
* :class:`MGPSidecarUnavailable` — raised when the optional dep is absent.

Concrete dataclasses (``RecallOutcome``, ``CommitOutcome``, etc.) are also
re-exported because they are safe to import without ``mgp_client``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    CommitOutcome,
    ParsedFact,
    RecallIntent,
    RecallItem,
    RecallOutcome,
    RuntimeState,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from nanobot.config.schema import MGPConfig

    from .sidecar import AsyncMGPSidecar


class MGPSidecarUnavailable(RuntimeError):
    """Raised when MGP sidecar is requested but ``mgp-client`` is not installed."""


def build_sidecar(config: "MGPConfig", *, workspace_id: str | None = None) -> "AsyncMGPSidecar":
    """Construct an :class:`AsyncMGPSidecar`. Imports ``mgp_client`` lazily.

    Raises :class:`MGPSidecarUnavailable` with an actionable hint when the
    optional dependency is missing — callers in ``AgentLoop.__init__`` are
    expected to guard with ``if mgp_config.enabled`` before invoking this.
    """
    try:
        import mgp_client  # noqa: F401  (probe import only)
    except ImportError as exc:
        raise MGPSidecarUnavailable(
            "mgp-client is not installed. Install with: pip install 'nanobot[mgp]'"
        ) from exc

    # Defer the heavy import until after the dependency check so a missing
    # mgp-client surfaces as a clear MGPSidecarUnavailable rather than a
    # generic ImportError raised deep inside the package import chain.
    from .sidecar import AsyncMGPSidecar

    return AsyncMGPSidecar(config, workspace_id=workspace_id)


__all__ = [
    "CommitOutcome",
    "MGPSidecarUnavailable",
    "ParsedFact",
    "RecallIntent",
    "RecallItem",
    "RecallOutcome",
    "RuntimeState",
    "build_sidecar",
]
