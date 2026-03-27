"""DG-Lab Coyote 3.0 V3 BLE protocol constants and helpers."""

# BLE UUIDs (base: 0000xxxx-0000-1000-8000-00805f9b34fb)
SERVICE_UUID = "0000180c-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000150a-0000-1000-8000-00805f9b34fb"   # B0/BF commands
NOTIFY_UUID = "0000150b-0000-1000-8000-00805f9b34fb"   # B1 responses
BATTERY_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
BATTERY_UUID = "00001500-0000-1000-8000-00805f9b34fb"

# Device name prefix
DEVICE_NAME_PREFIX = "47L121"

# Strength range
STRENGTH_MIN = 0
STRENGTH_MAX = 200

# Wave frequency range (input: 10~1000, encoded: 10~240)
WAVE_FREQ_MIN = 10
WAVE_FREQ_MAX = 1000
WAVE_FREQ_ENCODED_MIN = 10
WAVE_FREQ_ENCODED_MAX = 240

# Wave intensity range
WAVE_INTENSITY_MIN = 0
WAVE_INTENSITY_MAX = 100

# Strength interpretation modes (2 bits per channel)
STRENGTH_NONE = 0b00      # no change
STRENGTH_INCREASE = 0b01  # relative increase
STRENGTH_DECREASE = 0b10  # relative decrease
STRENGTH_ABSOLUTE = 0b11  # absolute set


def encode_frequency(freq_ms: int) -> int:
    """Convert frequency in ms (10~1000) to encoded value (10~240).

    Uses the V3 compression algorithm from the SDK.
    """
    if freq_ms < 10 or freq_ms > 1000:
        return 10
    if freq_ms <= 100:
        return freq_ms
    if freq_ms <= 600:
        return (freq_ms - 100) // 5 + 100
    return (freq_ms - 600) // 10 + 200


def build_b0(
    seq: int,
    strength_mode: int,
    strength_a: int,
    strength_b: int,
    wave_freq_a: tuple[int, int, int, int],
    wave_int_a: tuple[int, int, int, int],
    wave_freq_b: tuple[int, int, int, int],
    wave_int_b: tuple[int, int, int, int],
) -> bytes:
    """Build a 20-byte B0 instruction.

    Args:
        seq: Sequence number (0~15, 4 bits)
        strength_mode: Strength interpretation (4 bits: high 2 = A, low 2 = B)
        strength_a: A channel strength setting (0~200)
        strength_b: B channel strength setting (0~200)
        wave_freq_a: A channel wave frequencies x4 (encoded 10~240)
        wave_int_a: A channel wave intensities x4 (0~100)
        wave_freq_b: B channel wave frequencies x4 (encoded 10~240)
        wave_int_b: B channel wave intensities x4 (0~100)
    """
    header = 0xB0
    seq_and_mode = ((seq & 0x0F) << 4) | (strength_mode & 0x0F)

    data = bytearray(20)
    data[0] = header
    data[1] = seq_and_mode
    data[2] = min(max(strength_a, 0), 200)
    data[3] = min(max(strength_b, 0), 200)

    for i in range(4):
        data[4 + i] = min(max(wave_freq_a[i], 0), 255)
        data[8 + i] = min(max(wave_int_a[i], 0), 255)
        data[12 + i] = min(max(wave_freq_b[i], 0), 255)
        data[16 + i] = min(max(wave_int_b[i], 0), 255)

    return bytes(data)


def build_bf(
    limit_a: int,
    limit_b: int,
    balance_freq_a: int = 160,
    balance_freq_b: int = 160,
    balance_int_a: int = 0,
    balance_int_b: int = 0,
) -> bytes:
    """Build a 7-byte BF instruction.

    Args:
        limit_a: A channel strength soft limit (0~200)
        limit_b: B channel strength soft limit (0~200)
        balance_freq_a: A channel frequency balance param (0~255)
        balance_freq_b: B channel frequency balance param (0~255)
        balance_int_a: A channel intensity balance param (0~255)
        balance_int_b: B channel intensity balance param (0~255)
    """
    return bytes([
        0xBF,
        min(max(limit_a, 0), 200),
        min(max(limit_b, 0), 200),
        min(max(balance_freq_a, 0), 255),
        min(max(balance_freq_b, 0), 255),
        min(max(balance_int_a, 0), 255),
        min(max(balance_int_b, 0), 255),
    ])


def parse_b1(data: bytes) -> dict:
    """Parse a B1 notification response.

    Returns dict with seq, strength_a, strength_b.
    """
    if len(data) < 4 or data[0] != 0xB1:
        return {}
    return {
        "seq": data[1],
        "strength_a": data[2],
        "strength_b": data[3],
    }


# Inactive wave data: intensity > 100 causes channel to be ignored
WAVE_INACTIVE = (0, 0, 0, 101)
WAVE_FREQ_ZERO = (0, 0, 0, 0)
