"""Pure domain contracts for Fusion Reader v2."""

from .jobs import BackgroundJob, JobRegistry, JobState

__all__ = ["BackgroundJob", "JobRegistry", "JobState"]
