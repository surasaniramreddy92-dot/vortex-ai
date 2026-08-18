"""docs/REFACTOR_PLAN.md Step 5: the concrete Windows implementation of
PlatformAdapter - the literal shutdown/restart/lock commands, moved as-is
from main.py's system_shutdown/system_restart/lock_system (not redesigned;
same subprocess.Popen(..., shell=True) calls as before)."""
import subprocess

from ..base import PlatformAdapter


class WindowsPlatformAdapter(PlatformAdapter):
    def shutdown(self):
        subprocess.Popen('shutdown /s /t 5', shell=True)

    def restart(self):
        subprocess.Popen('shutdown /r /t 5', shell=True)

    def lock(self):
        subprocess.Popen('rundll32.exe user32.dll,LockWorkStation', shell=True)
