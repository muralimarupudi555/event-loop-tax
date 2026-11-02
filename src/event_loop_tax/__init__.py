"""Measure what one blocking call costs an async service.

`async def` is not a performance decision. What you call inside it is. This package
measures the difference, and the difference is large enough to be the whole story.
"""

from event_loop_tax.config import ConfigError, ScenarioConfig

__all__ = ["ScenarioConfig", "ConfigError"]
__version__ = "0.1.0"
