"""MCP Server for DG-Lab Coyote 3.0."""

import json
import logging

from mcp.server.fastmcp import FastMCP

from .device import CoyoteDevice
from .protocol import encode_frequency
from .waves import PRESETS, custom_wave_to_frames, preset_to_frames, steps_to_frames

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "DG-Lab Coyote 3.0",
    instructions=(
        "Control a DG-Lab Coyote 3.0 pulse device via Bluetooth. "
        "Typical workflow: scan → connect → set_strength → send_wave. "
        "Strength range is 0~200. Always start low (e.g. 5~10) and increase gradually. "
        "Available wave presets: breath, tide, pulse_low, pulse_mid, pulse_high, tap."
    ),
)

device = CoyoteDevice()


@mcp.tool()
async def scan(timeout: float = 5.0) -> str:
    """Scan for nearby DG-Lab Coyote 3.0 devices.

    Args:
        timeout: Scan duration in seconds (default 5)

    Returns:
        JSON list of found devices with name and address.
    """
    results = await device.scan(timeout=timeout)
    if not results:
        return "No Coyote devices found. Make sure the device is powered on."
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
async def connect(address: str) -> str:
    """Connect to a Coyote 3.0 device by Bluetooth address.

    Args:
        address: BLE address from scan results (e.g. "AA:BB:CC:DD:EE:FF")
    """
    try:
        await device.connect(address)
        return f"Connected to {address}. Battery: {device.state.battery}%"
    except Exception as e:
        return f"Connection failed: {e}"


@mcp.tool()
async def disconnect() -> str:
    """Disconnect from the current device."""
    await device.disconnect()
    return "Disconnected."


@mcp.tool()
async def set_strength(channel: str, value: int) -> str:
    """Set the absolute strength of a channel.

    SAFETY: Start with low values (5~10) and increase gradually.

    Args:
        channel: "A" or "B"
        value: Strength value (0~200)
    """
    if not device.state.connected:
        return "Error: Not connected to any device."
    if value < 0 or value > 200:
        return "Error: Strength must be 0~200."
    device.set_strength(channel.upper(), value)
    return f"Channel {channel.upper()} strength set to {value}."


@mcp.tool()
async def add_strength(channel: str, delta: int) -> str:
    """Increase or decrease the strength of a channel.

    Args:
        channel: "A" or "B"
        delta: Amount to change (positive = increase, negative = decrease)
    """
    if not device.state.connected:
        return "Error: Not connected to any device."
    device.add_strength(channel.upper(), delta)
    direction = "increased" if delta > 0 else "decreased"
    return f"Channel {channel.upper()} strength {direction} by {abs(delta)}."


@mcp.tool()
async def set_strength_limit(limit_a: int, limit_b: int) -> str:
    """Set strength soft limits for safety. Persisted on device even after power off.

    Args:
        limit_a: Max strength for A channel (0~200)
        limit_b: Max strength for B channel (0~200)
    """
    if not device.state.connected:
        return "Error: Not connected to any device."
    await device.set_strength_limit(limit_a, limit_b)
    return f"Strength limits set: A={limit_a}, B={limit_b}."


@mcp.tool()
async def send_wave(
    channel: str,
    preset: str | None = None,
    frequency: int | None = None,
    intensity: int | None = None,
    duration_frames: int = 10,
    loop: bool = True,
) -> str:
    """Send a waveform to a channel.

    Either use a preset name OR specify custom frequency/intensity.

    Args:
        channel: "A" or "B"
        preset: Preset name (breath, tide, pulse_low, pulse_mid, pulse_high, tap). Mutually exclusive with frequency/intensity.
        frequency: Custom wave frequency in ms (10~1000). Used with intensity.
        intensity: Custom wave intensity (0~100). Used with frequency.
        duration_frames: Number of 100ms frames for custom wave (default 10 = 1 second)
        loop: Whether to loop the waveform (default true)
    """
    if not device.state.connected:
        return "Error: Not connected to any device."

    if preset:
        try:
            frames = preset_to_frames(preset)
        except ValueError as e:
            return str(e)
    elif frequency is not None and intensity is not None:
        encoded_freq = encode_frequency(frequency)
        frames = custom_wave_to_frames(
            freq=encoded_freq,
            intensity=min(max(intensity, 0), 100),
            count=duration_frames,
        )
    else:
        return "Error: Provide either 'preset' or both 'frequency' and 'intensity'."

    device.send_wave(channel.upper(), frames, loop=loop)
    source = preset if preset else f"custom(freq={frequency}, int={intensity})"
    return f"Wave '{source}' started on channel {channel.upper()}, loop={loop}."


@mcp.tool()
async def design_wave(
    channel: str,
    steps: list[dict],
    loop: bool = True,
) -> str:
    """Design and play a custom waveform by defining a sequence of steps.

    This allows AI to create any waveform pattern: ramps, pulses, rhythms, etc.
    Each step represents 100ms of output.

    Args:
        channel: "A" or "B"
        steps: List of step objects, each with:
            - freq: wave frequency in ms (10~1000, lower = higher frequency pulse)
            - intensity: wave intensity (0~100, 0=silent, 100=strongest)
            - repeat: optional, repeat this step N times (default 1)
        loop: Whether to loop the waveform (default true)

    Example steps for a gradual ramp up then sudden drop:
        [
            {"freq": 10, "intensity": 0},
            {"freq": 10, "intensity": 25},
            {"freq": 10, "intensity": 50},
            {"freq": 10, "intensity": 75},
            {"freq": 10, "intensity": 100, "repeat": 3},
            {"freq": 10, "intensity": 0, "repeat": 2}
        ]
    """
    if not device.state.connected:
        return "Error: Not connected to any device."
    if not steps:
        return "Error: steps list cannot be empty."
    try:
        frames = steps_to_frames(steps)
    except (KeyError, TypeError) as e:
        return f"Error: Invalid step format: {e}. Each step needs 'freq' and 'intensity'."
    device.send_wave(channel.upper(), frames, loop=loop)
    return f"Custom wave ({len(frames)} frames, {len(frames)*100}ms) started on channel {channel.upper()}, loop={loop}."


@mcp.tool()
async def stop_wave(channel: str | None = None) -> str:
    """Stop waveform output.

    Args:
        channel: "A", "B", or omit to stop both channels
    """
    if not device.state.connected:
        return "Error: Not connected to any device."
    device.stop_wave(channel.upper() if channel else None)
    target = channel.upper() if channel else "both channels"
    return f"Wave stopped on {target}."


@mcp.tool()
async def get_status() -> str:
    """Get current device status including connection, strength, battery, and wave state."""
    status = device.get_status()
    return json.dumps(status, ensure_ascii=False)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
