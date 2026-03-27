"""BLE device manager for DG-Lab Coyote 3.0."""

import asyncio
import logging
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .protocol import (
    BATTERY_UUID,
    DEVICE_NAME_PREFIX,
    NOTIFY_UUID,
    SERVICE_UUID,
    STRENGTH_ABSOLUTE,
    STRENGTH_DECREASE,
    STRENGTH_INCREASE,
    STRENGTH_MAX,
    STRENGTH_MIN,
    STRENGTH_NONE,
    WAVE_FREQ_ZERO,
    WAVE_INACTIVE,
    WRITE_UUID,
    build_b0,
    build_bf,
    parse_b1,
)
from .waves import WaveFrame

logger = logging.getLogger(__name__)


@dataclass
class DeviceState:
    """Current device state."""
    connected: bool = False
    address: str = ""
    name: str = ""
    strength_a: int = 0
    strength_b: int = 0
    limit_a: int = 200
    limit_b: int = 200
    battery: int = -1

    # Pending strength changes (accumulated between B0 writes)
    _pending_strength_a: int = 0
    _pending_strength_b: int = 0
    _absolute_a: int | None = None
    _absolute_b: int | None = None

    # Wave playback state per channel
    wave_a: list[WaveFrame] = field(default_factory=list)
    wave_b: list[WaveFrame] = field(default_factory=list)
    wave_a_index: int = 0
    wave_b_index: int = 0
    wave_a_loop: bool = True
    wave_b_loop: bool = True

    # Sequence tracking
    _seq: int = 0
    _awaiting_seq: int | None = None


class CoyoteDevice:
    """Manages BLE connection and communication with Coyote 3.0."""

    def __init__(self) -> None:
        self.state = DeviceState()
        self._client: BleakClient | None = None
        self._loop_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def scan(self, timeout: float = 5.0) -> list[dict]:
        """Scan for nearby Coyote devices.

        Returns list of {name, address} dicts.
        """
        devices = await BleakScanner.discover(timeout=timeout)
        results = []
        for d in devices:
            name = d.name or ""
            if name.startswith(DEVICE_NAME_PREFIX):
                results.append({"name": name, "address": d.address})
        return results

    async def connect(self, address: str) -> None:
        """Connect to a Coyote device by address."""
        if self.state.connected:
            raise RuntimeError("Already connected. Disconnect first.")

        self._client = BleakClient(address)
        await self._client.connect()

        if not self._client.is_connected:
            raise RuntimeError(f"Failed to connect to {address}")

        self.state.connected = True
        self.state.address = address

        # Subscribe to notifications (B1 strength feedback)
        await self._client.start_notify(NOTIFY_UUID, self._on_notify)

        # Read battery
        try:
            battery_data = await self._client.read_gatt_char(BATTERY_UUID)
            if battery_data:
                self.state.battery = battery_data[0]
        except Exception:
            logger.debug("Could not read battery level")

        # Set default safety limits
        await self._write_bf()

        # Start the 100ms B0 write loop
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._b0_loop())

        logger.info("Connected to %s", address)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._loop_task:
            self._stop_event.set()
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        if self._client and self._client.is_connected:
            await self._client.disconnect()

        self.state = DeviceState()
        self._client = None
        logger.info("Disconnected")

    # --- Strength control ---

    def set_strength(self, channel: str, value: int) -> None:
        """Set absolute strength for a channel (0~200)."""
        value = max(STRENGTH_MIN, min(STRENGTH_MAX, value))
        if channel.upper() == "A":
            self.state._absolute_a = value
        elif channel.upper() == "B":
            self.state._absolute_b = value
        else:
            raise ValueError(f"Invalid channel: {channel}")

    def add_strength(self, channel: str, delta: int) -> None:
        """Add/subtract strength for a channel."""
        if channel.upper() == "A":
            self.state._absolute_a = None  # cancel any pending absolute
            self.state._pending_strength_a += delta
        elif channel.upper() == "B":
            self.state._absolute_b = None
            self.state._pending_strength_b += delta
        else:
            raise ValueError(f"Invalid channel: {channel}")

    async def set_strength_limit(self, limit_a: int, limit_b: int) -> None:
        """Set strength soft limits (persisted on device)."""
        self.state.limit_a = max(0, min(200, limit_a))
        self.state.limit_b = max(0, min(200, limit_b))
        await self._write_bf()

    # --- Wave control ---

    def send_wave(
        self,
        channel: str,
        frames: list[WaveFrame],
        loop: bool = True,
    ) -> None:
        """Start playing waveform frames on a channel."""
        if channel.upper() == "A":
            self.state.wave_a = frames
            self.state.wave_a_index = 0
            self.state.wave_a_loop = loop
        elif channel.upper() == "B":
            self.state.wave_b = frames
            self.state.wave_b_index = 0
            self.state.wave_b_loop = loop
        else:
            raise ValueError(f"Invalid channel: {channel}")

    def stop_wave(self, channel: str | None = None) -> None:
        """Stop waveform on a channel (or both if None)."""
        if channel is None or channel.upper() == "A":
            self.state.wave_a = []
            self.state.wave_a_index = 0
        if channel is None or channel.upper() == "B":
            self.state.wave_b = []
            self.state.wave_b_index = 0

    # --- Internal ---

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        """Handle BLE notifications from the device."""
        result = parse_b1(bytes(data))
        if result:
            self.state.strength_a = result["strength_a"]
            self.state.strength_b = result["strength_b"]
            # If this is a response to our strength change, allow next change
            if (
                self.state._awaiting_seq is not None
                and result["seq"] == self.state._awaiting_seq
            ):
                self.state._awaiting_seq = None
            logger.debug(
                "B1: seq=%d A=%d B=%d",
                result["seq"],
                result["strength_a"],
                result["strength_b"],
            )

    async def _write_bf(self) -> None:
        """Write BF instruction to set limits and balance params."""
        if not self._client or not self._client.is_connected:
            return
        data = build_bf(self.state.limit_a, self.state.limit_b)
        await self._client.write_gatt_char(WRITE_UUID, data)
        logger.debug("BF written: limit_a=%d limit_b=%d", self.state.limit_a, self.state.limit_b)

    def _build_next_b0(self) -> bytes:
        """Build the next B0 instruction from current state."""
        seq = 0
        strength_mode = 0
        sa = 0
        sb = 0

        # Handle strength changes (only if not awaiting response)
        if self.state._awaiting_seq is None:
            # A channel
            if self.state._absolute_a is not None:
                mode_a = STRENGTH_ABSOLUTE
                sa = self.state._absolute_a
                self.state._absolute_a = None
                self.state._seq = (self.state._seq % 15) + 1
                seq = self.state._seq
            elif self.state._pending_strength_a != 0:
                delta = self.state._pending_strength_a
                if delta > 0:
                    mode_a = STRENGTH_INCREASE
                    sa = delta
                else:
                    mode_a = STRENGTH_DECREASE
                    sa = -delta
                self.state._pending_strength_a = 0
                self.state._seq = (self.state._seq % 15) + 1
                seq = self.state._seq
            else:
                mode_a = STRENGTH_NONE

            # B channel
            if self.state._absolute_b is not None:
                mode_b = STRENGTH_ABSOLUTE
                sb = self.state._absolute_b
                self.state._absolute_b = None
                if seq == 0:
                    self.state._seq = (self.state._seq % 15) + 1
                    seq = self.state._seq
            elif self.state._pending_strength_b != 0:
                delta = self.state._pending_strength_b
                if delta > 0:
                    mode_b = STRENGTH_INCREASE
                    sb = delta
                else:
                    mode_b = STRENGTH_DECREASE
                    sb = -delta
                self.state._pending_strength_b = 0
                if seq == 0:
                    self.state._seq = (self.state._seq % 15) + 1
                    seq = self.state._seq
            else:
                mode_b = STRENGTH_NONE

            strength_mode = (mode_a << 2) | mode_b

            if seq > 0:
                self.state._awaiting_seq = seq
        else:
            mode_a = STRENGTH_NONE
            mode_b = STRENGTH_NONE

        # Wave data for A channel
        if self.state.wave_a:
            idx = self.state.wave_a_index
            frame = self.state.wave_a[idx]
            wave_freq_a = frame.freq
            wave_int_a = frame.intensity
            # Advance index
            next_idx = idx + 1
            if next_idx >= len(self.state.wave_a):
                if self.state.wave_a_loop:
                    next_idx = 0
                else:
                    self.state.wave_a = []
                    next_idx = 0
            self.state.wave_a_index = next_idx
        else:
            wave_freq_a = WAVE_FREQ_ZERO
            wave_int_a = WAVE_INACTIVE

        # Wave data for B channel
        if self.state.wave_b:
            idx = self.state.wave_b_index
            frame = self.state.wave_b[idx]
            wave_freq_b = frame.freq
            wave_int_b = frame.intensity
            next_idx = idx + 1
            if next_idx >= len(self.state.wave_b):
                if self.state.wave_b_loop:
                    next_idx = 0
                else:
                    self.state.wave_b = []
                    next_idx = 0
            self.state.wave_b_index = next_idx
        else:
            wave_freq_b = WAVE_FREQ_ZERO
            wave_int_b = WAVE_INACTIVE

        return build_b0(
            seq=seq,
            strength_mode=strength_mode,
            strength_a=sa,
            strength_b=sb,
            wave_freq_a=wave_freq_a,
            wave_int_a=wave_int_a,
            wave_freq_b=wave_freq_b,
            wave_int_b=wave_int_b,
        )

    async def _b0_loop(self) -> None:
        """100ms periodic loop to send B0 instructions."""
        while not self._stop_event.is_set():
            try:
                if self._client and self._client.is_connected:
                    data = self._build_next_b0()
                    await self._client.write_gatt_char(WRITE_UUID, data)
                else:
                    break
            except Exception as e:
                logger.error("B0 loop error: %s", e)
                break
            await asyncio.sleep(0.1)

        # Connection lost in loop
        if self.state.connected:
            self.state.connected = False
            logger.warning("Connection lost in B0 loop")

    def get_status(self) -> dict:
        """Get current device status."""
        return {
            "connected": self.state.connected,
            "address": self.state.address,
            "strength_a": self.state.strength_a,
            "strength_b": self.state.strength_b,
            "limit_a": self.state.limit_a,
            "limit_b": self.state.limit_b,
            "battery": self.state.battery,
            "wave_a_active": len(self.state.wave_a) > 0,
            "wave_b_active": len(self.state.wave_b) > 0,
        }
