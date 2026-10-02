import time
import uuid
from typing import Dict, Any, Optional
from config import TASK_TTL_SECONDS

class TaskManager:
    """In-memory cache for pending download tasks to bypass Telegram 64-byte callback limit."""
    
    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def create_task(self, data: Dict[str, Any]) -> str:
        """Saves task data and returns a unique short task ID."""
        self.cleanup_expired()
        task_id = uuid.uuid4().hex[:8]
        data['created_at'] = time.time()
        self._tasks[task_id] = data
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves task data by ID."""
        return self._tasks.get(task_id)

    def get_latest_task_for_chat(self, chat_id: int) -> Optional[tuple[str, Dict[str, Any]]]:
        """Finds the most recent task created for a specific chat."""
        matching = [
            (k, v) for k, v in self._tasks.items()
            if v.get('chat_id') == chat_id
        ]
        if not matching:
            return None
        # Sort by created_at descending
        matching.sort(key=lambda item: item[1].get('created_at', 0), reverse=True)
        return matching[0]

    def remove_task(self, task_id: str):
        """Removes task data."""
        self._tasks.pop(task_id, None)

    def cleanup_expired(self):
        """Removes tasks older than TASK_TTL_SECONDS."""
        now = time.time()
        expired_keys = [
            k for k, v in self._tasks.items() 
            if now - v.get('created_at', 0) > TASK_TTL_SECONDS
        ]
        for k in expired_keys:
            self._tasks.pop(k, None)

task_manager = TaskManager()
