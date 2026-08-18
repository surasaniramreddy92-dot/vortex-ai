"""VORTEX entry point.

docs/REFACTOR_PLAN.md Step 8: main.py is now purely the bootstrap - build
the app, run it. Steps 1-7 moved everything else (config, voice, LLM
provider, platform adapter, capability registry, orchestrator/state
manager) into their own modules; src/vortex/app.py is what used to live
here, module-level constants and all."""
from .app import Vortex


def main():
    app = Vortex()
    app.start()


if __name__ == '__main__':
    main()
