"""sentry-rice — RICE-prioritise Sentry issues, with AI scoring that traces each
issue into your codebase.

The deterministic engine (reach, RICE, sync, web UI, overrides, scan/New
tracking, resolve) lives here and is fully configurable via a single YAML file.
The AI scoring layer is Claude-Code-native (see the bundled .claude templates).
"""
from sentryrice.config import Config, load_config

__version__ = "0.1.1"
__all__ = ["Config", "load_config", "__version__"]
