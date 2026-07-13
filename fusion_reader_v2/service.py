"""Compatibility import for the public Fusion Reader facade.

New code should import :class:`FusionReaderV2` from ``fusion_reader_v2`` or
``fusion_reader_v2.facade``. This module remains deliberately thin for
historical callers.
"""

from .facade import FusionReaderV2, VoiceSettings

__all__ = ["FusionReaderV2", "VoiceSettings"]
