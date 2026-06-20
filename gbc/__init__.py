"""gbc — recover a chaotic music library into a clean album library (beets-driven).

One core, several front doors: `run` (manual) and `inbox` (cron) call the SAME pipeline;
only the trigger and the scope differ, never the logic.
"""
__version__ = "0.5.0"
