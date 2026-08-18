"""docs/REFACTOR_PLAN.md Step 5: separates *what* VORTEX wants to do (shut
down, restart, lock the machine) from *how* the current OS does it, so a
non-Windows implementation is an addition later, not a rewrite of call
sites in main.py."""
from abc import ABC, abstractmethod


class PlatformAdapter(ABC):
    @abstractmethod
    def shutdown(self):
        raise NotImplementedError

    @abstractmethod
    def restart(self):
        raise NotImplementedError

    @abstractmethod
    def lock(self):
        raise NotImplementedError
