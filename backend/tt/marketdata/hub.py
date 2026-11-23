"""In-process pub/sub for live updates (WebSocket fan-out).

Topics: "quotes:<instrument_id>", "candles:1m:<instrument_id>", "alerts".
Slow subscribers never block publishers: when a queue is full the oldest message is dropped.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class Subscription:
    def __init__(self, hub: "Hub", maxsize: int = 1000):
        self.hub = hub
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.topics: set[str] = set()
        self.dropped = 0

    def add(self, topic: str) -> None:
        self.topics.add(topic)
        self.hub._subs[topic].add(self)

    def remove(self, topic: str) -> None:
        self.topics.discard(topic)
        self.hub._subs[topic].discard(self)

    def close(self) -> None:
        for t in list(self.topics):
            self.remove(t)

    def _offer(self, item: tuple[str, Any]) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(item)


class Hub:
    def __init__(self):
        self._subs: dict[str, set[Subscription]] = defaultdict(set)

    def subscribe(self) -> Subscription:
        return Subscription(self)

    def publish(self, topic: str, data: Any) -> None:
        for sub in list(self._subs.get(topic, ())):
            sub._offer((topic, data))

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))
