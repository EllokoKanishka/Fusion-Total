"""Stable public facade import.

The implementation remains compatible through ``fusion_reader_v2.service``
while services are extracted behind this boundary.
"""

from .service import FusionReaderV2, VoiceSettings

__all__ = ["FusionReaderV2", "VoiceSettings"]
