"""JSON payload helpers for decision requests: image parts, nesting limits and walking.

State, instructions and criteria are arbitrary JSON that may hold inline image parts
(``{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}``) at any
depth. These helpers normalise such values to plain JSON, enforce the documented
nesting limit and find or blank out image payloads.
"""

from __future__ import annotations

import base64
import math
import re
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

MAX_NESTING_DEPTH = 64
IMAGE_PART_TYPE: Literal["image_url"] = "image_url"

ImageMime = Literal["image/png", "image/jpeg"]

_DATA_URL_PREFIX = re.compile(r"data:image/(png|jpeg);base64,")
_BASE64_BODY = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def check_data_url(url: str) -> str:
    """Return ``url`` if it is a base64 PNG or JPEG data URL, else raise ``ValueError``."""
    prefix = _DATA_URL_PREFIX.match(url)
    if prefix is None:
        raise ValueError("image url must be a base64 data URL of type image/png or image/jpeg")
    if not _BASE64_BODY.fullmatch(url, prefix.end()):
        raise ValueError("image url must carry a non-empty standard base64 payload")
    return url


class ImageURL(BaseModel):
    """The ``image_url`` object of an image part."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    detail: Literal["auto", "low", "high"] | None = None

    @field_validator("url")
    @classmethod
    def _valid_data_url(cls, value: str) -> str:
        return check_data_url(value)


class ImagePart(BaseModel):
    """An inline image, in the same shape chat-completion message content uses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["image_url"] = IMAGE_PART_TYPE
    image_url: ImageURL

    @classmethod
    def from_bytes(cls, data: bytes, mime: ImageMime) -> ImagePart:
        """Build an image part from raw PNG or JPEG bytes."""
        encoded = base64.b64encode(data).decode("ascii")
        return cls(image_url=ImageURL(url=f"data:{mime};base64,{encoded}"))

    def to_wire(self) -> dict[str, Any]:
        """Return the JSON shape sent to decision backends."""
        return self.model_dump(mode="json", exclude_none=True)


def is_image_part(value: Any) -> bool:
    """True when ``value`` is a dict that declares itself an image part."""
    return isinstance(value, dict) and value.get("type") == IMAGE_PART_TYPE


def normalize_json(value: Any, *, where: str) -> Any:
    """Return a plain-JSON copy of ``value``.

    Pydantic models (such as :class:`ImagePart`) are dumped to dicts, tuples become
    lists, image parts are validated, and nesting deeper than
    :data:`MAX_NESTING_DEPTH` containers raises ``ValueError``. A flat dict or list
    has depth 1.
    """
    return _normalize(value, 0, where)


def _normalize(value: Any, depth: int, where: str) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return _normalize_dict(value, _deeper(depth, where), where)
    if isinstance(value, (list, tuple)):
        level = _deeper(depth, where)
        return [_normalize(item, level, where) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{where} holds a non-finite number")
        return value
    raise ValueError(f"{where} holds a value of type {type(value).__name__}, which is not JSON")


def _normalize_dict(value: dict[Any, Any], depth: int, where: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{where} has a non-string key {key!r}")
        result[key] = _normalize(item, depth, where)
    if is_image_part(result):
        ImagePart.model_validate(result)
    return result


def _deeper(depth: int, where: str) -> int:
    if depth + 1 > MAX_NESTING_DEPTH:
        raise ValueError(f"{where} nests deeper than {MAX_NESTING_DEPTH} levels")
    return depth + 1


def iter_image_parts(value: Any) -> Iterator[dict[str, Any]]:
    """Yield every image part inside a normalised JSON value, at any depth."""
    if is_image_part(value):
        yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_image_parts(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_image_parts(item)


def strip_image_payloads(value: Any) -> Any:
    """Return a copy of a normalised JSON value with every image part's URL blanked."""
    if is_image_part(value):
        return {**value, "image_url": {**value.get("image_url", {}), "url": ""}}
    if isinstance(value, dict):
        return {key: strip_image_payloads(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_image_payloads(item) for item in value]
    return value
