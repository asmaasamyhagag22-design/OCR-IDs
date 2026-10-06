"""Spawned engine worker. The existing recognizer supplies every pipeline result."""

from __future__ import annotations

import math
import os
from multiprocessing.connection import Connection
from typing import Any


def worker_main(channel: Connection, threads: int) -> None:
    """Load once, observe completed stages, and send readings only over a private pipe."""
    if os.name != "posix":
        channel.send(("failed",))
        return
    # Native libraries can print arbitrary values. Never expose their output to service logs.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    from .hardening import no_core_dumps
    from .runtime import ServiceRuntime, configure_threads

    no_core_dumps()
    configure_threads(threads)
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_v3_shadow as v3
    from src.extraction.admission_second_pass import structurally_valid

    from . import golden
    from .recognizer import ImageTooLarge, Recognizer, UndecodableImage

    observing = False
    best_rank = (False, float("-inf"))

    def checkpoint(reading: str | None, margin: float | None) -> None:
        nonlocal best_rank
        if reading and observing:
            value = float(margin) if margin is not None and math.isfinite(float(margin)) else float("-inf")
            rank = (structurally_valid(reading), value)
            if rank < best_rank:
                return
            best_rank = rank
            channel.send(("partial", reading, margin))

    class ObservedRuntime(ServiceRuntime):
        """Observe every completed BC-final prediction, including candidates within F8/F9."""

        def predict_bcfinal(self, tensor: Any) -> Any:
            result = super().predict_bcfinal(tensor)
            if observing:
                checkpoint(v3._digits(result), shadow.weakest_margin(result))
            return result

    class ObservedRecognizer(Recognizer):
        """Delegate unchanged stages and observe their already completed readings."""

        def _first_pass_with_f1v(self, rgb: Any, fixes: list[str]) -> tuple[Any, Any, Any, Any, Any]:
            result = super()._first_pass_with_f1v(rgb, fixes)
            current = result[0]
            if current is not None:
                checkpoint(getattr(current, "bc_final_national_id", None), getattr(current, "bc_final_margin", None))
            return result

        def _f9(self, rgb: Any, **kwargs: Any) -> Any:
            checkpoint(kwargs.get("reading"), kwargs.get("margin"))
            result = super()._f9(rgb, **kwargs)
            if result is not None:
                checkpoint(result.reading, result.margin)
            return result

    try:
        recognizer = ObservedRecognizer(runtime=ObservedRuntime())
        recognizer.load()
        golden.run(recognizer.runtime)
        channel.send(("ready",))
    except Exception:  # noqa: BLE001 - no exception values or traces cross the pipe
        channel.send(("failed",))
        return
    try:
        while True:
            payload = channel.recv()
            if payload is None:
                return
            best_rank = (False, float("-inf"))
            observing = True
            try:
                result = recognizer.recognize(payload)
                channel.send(("result", result.reading, result.margin, result.reason_code))
            except ImageTooLarge:
                channel.send(("error", "IMAGE_TOO_LARGE"))
            except UndecodableImage:
                channel.send(("error", "UNREADABLE_IMAGE"))
            except Exception:  # noqa: BLE001 - an image failure never fails the whole batch
                channel.send(("error", "UNREADABLE_IMAGE"))
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        channel.close()
