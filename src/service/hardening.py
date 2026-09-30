"""Process hardening shared by the service and the CLI."""

from __future__ import annotations


def no_core_dumps() -> None:
    """A native crash must not write the image, the typed ID or the reading to disk (D-052).

    The run commands also pass `--ulimit core=0`; this covers a run without it.
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):  # not Linux, or not permitted
        pass
