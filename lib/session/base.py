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

    @abstractmethod
    def get_info(self, app_name) -> dict:
        """Return dict with uptime, pid, etc."""
        pass
