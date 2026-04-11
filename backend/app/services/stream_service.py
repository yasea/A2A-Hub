"""
基于内存队列的简单任务事件推送。
"""
import asyncio
from collections import defaultdict
from typing import Any


class TaskEventBroker:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(task_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(task_id, None)

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(task_id, ())):
            await queue.put(event)


task_event_broker = TaskEventBroker()
