from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import json
import math

from bubble.errors import BubbleError


@dataclass(frozen=True)
class RefineParams:
    opacity: float = 0.0
    fade_start_m: float = 4.0
    fade_end_m: float = 18.0
    angle_power: float = 0.0
    luminance_gain: float = 1.0
    luminance_bias: float = 0.0
    enabled_frames: tuple[str, ...] = ()


def _finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise BubbleError("value must be finite")
    return number


def apply_operator(params: RefineParams, name: str, value: str, cameras) -> RefineParams:
    if name == "set_opacity":
        number = _finite(value)
        if not 0 <= number <= 1:
            raise BubbleError("opacity out of range")
        return replace(params, opacity=number)
    if name == "set_fade_start_m":
        number = _finite(value)
        if number < 0 or number >= params.fade_end_m:
            raise BubbleError("fade_start_m out of range")
        return replace(params, fade_start_m=number)
    if name == "set_fade_end_m":
        number = _finite(value)
        if number <= params.fade_start_m or number > 500:
            raise BubbleError("fade_end_m out of range")
        return replace(params, fade_end_m=number)
    if name == "set_angle_power":
        number = _finite(value)
        if not 0 <= number <= 4:
            raise BubbleError("angle_power out of range")
        return replace(params, angle_power=number)
    if name == "set_luminance_gain":
        number = _finite(value)
        if not 0 <= number <= 4:
            raise BubbleError("luminance_gain out of range")
        return replace(params, luminance_gain=number)
    if name == "set_luminance_bias":
        number = _finite(value)
        if not -255 <= number <= 255:
            raise BubbleError("luminance_bias out of range")
        return replace(params, luminance_bias=number)
    if name == "enable_frame":
        match = [cam for cam in cameras if cam.name == value]
        if not match:
            raise BubbleError(f"unknown camera '{value}'")
        if match[0].role != "fit":
            raise BubbleError(f"cannot enable {match[0].role} camera '{value}'")
        if value in params.enabled_frames:
            return params
        return replace(params, enabled_frames=params.enabled_frames + (value,))
    if name == "disable_frame":
        return replace(
            params,
            enabled_frames=tuple(item for item in params.enabled_frames if item != value),
        )
    raise BubbleError(f"unknown operator '{name}'")


def save_params(path: Path, params: RefineParams) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "opacity": params.opacity,
        "fade_start_m": params.fade_start_m,
        "fade_end_m": params.fade_end_m,
        "angle_power": params.angle_power,
        "luminance_gain": params.luminance_gain,
        "luminance_bias": params.luminance_bias,
        "enabled_frames": list(params.enabled_frames),
    }, indent=2))


def load_params(path: Path) -> RefineParams:
    raw = json.loads(path.read_text())
    return RefineParams(
        opacity=raw["opacity"],
        fade_start_m=raw["fade_start_m"],
        fade_end_m=raw["fade_end_m"],
        angle_power=raw["angle_power"],
        luminance_gain=raw["luminance_gain"],
        luminance_bias=raw["luminance_bias"],
        enabled_frames=tuple(raw["enabled_frames"]),
    )
