"""A bounded spawn process pool with observable checkpoints and worker replacement."""

from __future__ import annotations

import asyncio
import math
import multiprocessing as mp
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from .engine_worker import worker_main


@dataclass
class Slot:
    """One process and its private, sequential request channel."""

    process: BaseProcess
    channel: Connection
    ready: bool = False


def front_result(
    reading: str | None = None,
    margin: float | None = None,
    error: str | None = None,
    success_min_margin: float = 0.0,
) -> dict[str, Any]:
    """The engine decision rule, separate from the existing v1 decider."""
    from src.extraction.admission_second_pass import structurally_valid

    from .versions import MODEL_VERSION

    confidence = float(margin) if margin is not None and math.isfinite(float(margin)) else None
    if error is None:
        if not reading:
            error = "UNREADABLE_IMAGE"
        elif not structurally_valid(reading):
            error = "INVALID_READING"
        elif success_min_margin > 0 and (confidence is None or confidence < success_min_margin):
            error = "LOW_CONFIDENCE"
    return {
        "finding": reading or None,
        "error_codes": [error] if error else [],
        "confidence": confidence,
        "model_version": MODEL_VERSION,
        "decision_rule_version": "phase4-engine-v2",
    }


class EngineBusy(Exception):
    """No batch capacity or no healthy, warm pool."""


class EnginePool:
    """One admitted batch at a time; parallel items, bounded waiting, no unbounded job queue."""

    def __init__(
        self,
        workers: int = 2,
        threads: int = 4,
        budget_s: float = 45.0,
        warmup_s: float = 130.0,
        target: Callable[[Connection, int], None] = worker_main,
        success_min_margin: float = 0.0,
    ) -> None:
        self.workers = workers
        self.threads = threads
        self.budget_s = budget_s
        self.warmup_s = warmup_s
        self.target = target
        self.success_min_margin = success_min_margin
        self.slots: list[Slot] = []
        self.available: asyncio.Queue[Slot] = asyncio.Queue()
        self.active = False
        self.first = True
        self.closed = False

    def _spawn(self) -> Slot:
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=self.target, args=(child, self.threads), daemon=True)
        process.start()
        child.close()
        return Slot(process, parent)

    def start(self) -> None:
        """Spawn each worker once. Loading continues independently of the HTTP event loop."""
        if self.slots:
            return
        for _ in range(self.workers):
            slot = self._spawn()
            self.slots.append(slot)
            self.available.put_nowait(slot)

    def admit(self) -> None:
        """Reserve the batch before reading its body. Saturation fails immediately."""
        if self.active or self.closed:
            raise EngineBusy
        self.active = True

    def release(self) -> None:
        """Release a batch reservation even on malformed input or client disconnect."""
        self.active = False

    async def _ready(self, slot: Slot, deadline: float) -> None:
        while not slot.ready:
            if slot.channel.poll():
                message = slot.channel.recv()
                if message[0] != "ready":
                    raise EngineBusy
                slot.ready = True
                return
            if not slot.process.is_alive() or time.monotonic() >= deadline:
                raise EngineBusy
            await asyncio.sleep(0.01)

    def _replace(self, slot: Slot) -> Slot:
        # terminate is nonblocking; joining native libraries must never block the HTTP loop.
        slot.process.terminate()
        slot.channel.close()
        replacement = self._spawn()
        self.slots[self.slots.index(slot)] = replacement
        return replacement

    async def _item(self, payload: bytes, deadline: float) -> dict[str, Any]:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            slot = await asyncio.wait_for(self.available.get(), remaining)
        except TimeoutError:
            return front_result(error="PROCESSING_TIMEOUT")
        reading = margin = None
        replace = False
        try:
            await self._ready(slot, deadline)
            await asyncio.wait_for(asyncio.to_thread(slot.channel.send, payload), max(0, deadline - time.monotonic()))
            while time.monotonic() < deadline:
                if slot.channel.poll():
                    message = slot.channel.recv()
                    if message[0] == "partial":
                        reading, margin = message[1:]
                    elif message[0] == "result":
                        reason = message[3]
                        error = reason if reason in ("CARD_NOT_FOUND", "UPLOAD_IS_VIDEO") else None
                        return front_result(message[1], message[2], error, self.success_min_margin)
                    elif message[0] == "error":
                        return front_result(error=message[1])
                elif not slot.process.is_alive():
                    raise EOFError
                await asyncio.sleep(0.005)
            replace = True
            return front_result(reading, margin, "PROCESSING_TIMEOUT")
        except (EngineBusy, TimeoutError):
            replace = True
            return front_result(error="PROCESSING_TIMEOUT")
        except (EOFError, BrokenPipeError, OSError):
            replace = True
            return front_result(error="UNREADABLE_IMAGE")
        except asyncio.CancelledError:
            replace = True
            raise
        finally:
            if not self.closed:
                self.available.put_nowait(self._replace(slot) if replace else slot)

    async def batch(self, payloads: list[bytes | str], request_started: float | None = None) -> list[dict[str, Any]]:
        """Warm once, then give every item the same deadline, including waiting for a process."""
        self.start()
        warming = self.first
        deadline = time.monotonic() + (self.warmup_s if warming else 0.1)
        try:
            await asyncio.gather(*(self._ready(slot, deadline) for slot in self.slots))
        except (EngineBusy, EOFError, OSError):
            raise EngineBusy from None
        self.first = False
        deadline = time.monotonic() + self.budget_s
        if request_started is not None:
            deadline = min(deadline, request_started + 49 + (self.warmup_s if warming else 0))

        async def item(value: bytes | str) -> dict[str, Any]:
            return front_result(error=value) if isinstance(value, str) else await self._item(value, deadline)

        return list(await asyncio.gather(*(item(value) for value in payloads)))

    def close(self) -> None:
        """Stop processes without waiting for in-flight native inference."""
        self.closed = True
        for slot in self.slots:
            if slot.process.is_alive():
                slot.process.terminate()
            slot.channel.close()
