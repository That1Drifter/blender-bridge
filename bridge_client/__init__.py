"""bpy-free client package for the Blender Bridge TCP service."""

from .client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    PROTOCOL_VERSION,
    SLOW_COMMANDS,
    BridgeClient,
    BridgeTransportError,
)
from .codec import HEADER_SIZE, MAX_MESSAGE_SIZE, FrameTooLargeError

__all__ = [
    "BridgeClient",
    "BridgeTransportError",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "HEADER_SIZE",
    "MAX_MESSAGE_SIZE",
    "FrameTooLargeError",
    "PROTOCOL_VERSION",
    "SLOW_COMMANDS",
]
