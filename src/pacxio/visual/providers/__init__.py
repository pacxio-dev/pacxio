from collections.abc import Callable

from pacxio.visual.providers.base import GenRequest, GenResult, ImageProvider, ProviderError
from pacxio.visual.providers.openai_api import OpenAIProvider

PROVIDERS: dict[str, Callable[[], ImageProvider]] = {"openai": OpenAIProvider}

__all__ = [
    "PROVIDERS",
    "GenRequest",
    "GenResult",
    "ImageProvider",
    "OpenAIProvider",
    "ProviderError",
]
