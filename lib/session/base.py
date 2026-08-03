from abc import ABC, abstractmethod


class BaseSessionManager(ABC):
    def __init__(self, global_config):
        self.config = global_config

    @abstractmethod
    def start(self, runner) -> tuple:
        pass

    @abstractmethod
    def stop(self, app_name) -> tuple:
        pass

    @abstractmethod
    def is_running(self, app_name) -> bool:
        pass

    def get_status_snapshot(self, app_ids) -> dict[str, dict]:
        """Return one status payload per stable app ID.

        Backends may override this to batch their storage access. The compatibility
        fallback deliberately preserves order and removes duplicate IDs.
        """
        snapshot = {}
        for app_id in dict.fromkeys(str(value) for value in app_ids):
            payload = self.get_info(app_id)
            snapshot[app_id] = payload or {"status": "STOPPED", "instances": 0}
        return snapshot

    @abstractmethod
    def get_info(self, app_name) -> dict:
        """Return dict with uptime, pid, etc."""
        pass
