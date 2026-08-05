"""Makita LXT command set, frame decoding and a hardware-agnostic client.

The decoding functions are pure: they take the raw bytes an adapter returned
and produce plain dictionaries. That keeps the wire format testable without a
battery on the bench, and lets the GUI and the ``obi-log`` CLI share one
implementation.
"""

# Command definitions
MODEL_CMD = [0x01, 0x02, 0x10, 0xCC, 0xDC, 0x0C]
READ_DATA_REQUEST = [0x01, 0x04, 0x1D, 0xCC, 0xD7, 0x00, 0x00, 0xFF]
TESTMODE_CMD = [0x01, 0x03, 0x09, 0x33, 0xD9, 0x96, 0xA5]
LEDS_ON_CMD = [0x01, 0x02, 0x09, 0x33, 0xDA, 0x31]
LEDS_OFF_CMD = [0x01, 0x02, 0x09, 0x33, 0xDA, 0x34]
RESET_ERROR_CMD = [0x01, 0x02, 0x09, 0x33, 0xDA, 0x04]
ROMID_CHARGER_CMD = [0x01, 0x02, 0x28, 0x33, 0xF0, 0x00]
CHARGER_CMD = [0x01, 0x02, 0x20, 0xCC, 0xF0, 0x00]
READ_MSG_CMD = [0x01, 0x02, 0x28, 0x33, 0xAA, 0x00]
CLEAR_CMD = [0x01, 0x02, 0x00, 0xCC, 0xF0, 0x00]
STORE_CMD = [0x01, 0x02, 0x00, 0x33, 0x55, 0xA5]
CLEAN_FRAME_CMD = [
    0x01, 0x22, 0x00, 0x33, 0x33, 0x0F, 0x00, 0xF1, 0x26, 0xBD, 0x13, 0x14,
    0x58, 0x00, 0x00, 0x94, 0x94, 0x40, 0x21, 0xD0, 0x80, 0x02, 0x4E, 0x23,
    0xD0, 0x8E, 0x45, 0x60, 0x1A, 0x00, 0x03, 0x02, 0x02, 0x0E, 0x20, 0x00,
    0x30, 0x01, 0x83,
]

# Commands specific to the F0513 version
F0513_VCELL_1_CMD = [0x01, 0x01, 0x02, 0xCC, 0x31]
F0513_VCELL_2_CMD = [0x01, 0x01, 0x02, 0xCC, 0x32]
F0513_VCELL_3_CMD = [0x01, 0x01, 0x02, 0xCC, 0x33]
F0513_VCELL_4_CMD = [0x01, 0x01, 0x02, 0xCC, 0x34]
F0513_VCELL_5_CMD = [0x01, 0x01, 0x02, 0xCC, 0x35]
F0513_VCELL_CMDS = [
    F0513_VCELL_1_CMD, F0513_VCELL_2_CMD, F0513_VCELL_3_CMD,
    F0513_VCELL_4_CMD, F0513_VCELL_5_CMD,
]
F0513_TEMP_CMD = [0x01, 0x01, 0x02, 0xCC, 0x52]
F0513_MODEL_CMD = [0x01, 0x00, 0x02, 0x31]
F0513_VERSION_CMD = [0x01, 0x00, 0x02, 0x32]
F0513_TESTMODE_CMD = [0x01, 0x01, 0x00, 0xCC, 0x99]

F0513 = "F0513"
CELL_COUNT = 5


def nibble_swap(byte):
    """Swap the high and low nibble of a byte."""
    return ((byte & 0xF0) >> 4) | ((byte & 0x0F) << 4)


def _u16le(data):
    return int.from_bytes(bytes(bytearray(data)), byteorder="little")


def _require(response, length, what):
    if response is None or len(response) < length:
        raise ValueError("Truncated %s response: got %d bytes, need %d"
                         % (what, 0 if response is None else len(response), length))


def decode_message_frame(response):
    """Decode the 42 byte answer to :data:`READ_MSG_CMD`."""
    _require(response, 42, "battery message")

    swapped = bytearray([nibble_swap(response[37]), nibble_swap(response[36])])[::-1]
    charge_count = int.from_bytes(bytes(swapped), byteorder="big") & 0x0FFF
    locked = (response[30] & 0x0F) > 0

    return {
        "rom_id": " ".join("%02X" % b for b in response[2:10]),
        "battery_message": " ".join("%02X" % b for b in response[10:42]),
        "charge_count": charge_count,
        "locked": locked,
        "state": "LOCKED" if locked else "UNLOCKED",
        "status_code": "%02X" % response[29],
        "manufacturing_date": "%02d/%02d/20%02d" % (response[4], response[3], response[2]),
        "capacity_ah": nibble_swap(response[26]) / 10,
        "battery_type": nibble_swap(response[21]),
    }


def decode_pack_data(response):
    """Decode the answer to :data:`READ_DATA_REQUEST` (standard batteries)."""
    _require(response, 20, "battery data")

    cells = [_u16le(response[4 + 2 * i:6 + 2 * i]) / 1000 for i in range(CELL_COUNT)]
    return _pack_dict(
        pack_v=_u16le(response[2:4]) / 1000,
        cells=cells,
        temp1=_u16le(response[16:18]) / 100,
        temp2=_u16le(response[18:20]) / 100,
    )


def decode_f0513_data(cell_responses, temp_response):
    """Decode the per-cell answers used by F0513 batteries.

    These batteries have no combined data frame, so the pack voltage is the
    sum of the cells and there is only one temperature sensor.
    """
    if len(cell_responses) != CELL_COUNT:
        raise ValueError("Expected %d cell responses, got %d"
                         % (CELL_COUNT, len(cell_responses)))
    cells = []
    for index, response in enumerate(cell_responses, start=1):
        _require(response, 4, "cell %d" % index)
        cells.append(_u16le(response[2:4]) / 1000)
    _require(temp_response, 4, "temperature")

    return _pack_dict(
        pack_v=sum(cells),
        cells=cells,
        temp1=_u16le(temp_response[2:4]) / 100,
        temp2=None,
    )


def _pack_dict(pack_v, cells, temp1, temp2):
    data = {
        "pack_v": round(pack_v, 3),
        "cell_delta_v": round(max(cells) - min(cells), 3),
        "temp1_c": temp1,
        "temp2_c": temp2,
    }
    for index, voltage in enumerate(cells, start=1):
        data["cell%d_v" % index] = voltage
    return data


def log_fieldnames(include_status=False):
    """Column order used by the CSV/JSONL logger."""
    fields = ["model", "rom_id", "pack_v"]
    fields += ["cell%d_v" % i for i in range(1, CELL_COUNT + 1)]
    fields += ["cell_delta_v", "temp1_c", "temp2_c"]
    if include_status:
        fields += ["state", "status_code", "charge_count"]
    return fields


class MakitaClient(object):
    """Talks to a Makita LXT pack over anything exposing ``request()``.

    That is either :class:`core.obi_link.ObiLink` (CLI) or the Tk interface
    widget (GUI), which delegates to the same link.
    """

    def __init__(self, link):
        self.link = link
        self.command_version = None
        self.model = None
        self.rom_id = None

    # ------------------------------------------------------------------
    def read_message(self, trace=True):
        """Read the battery message frame (ROM id, charge count, lock state)."""
        response = self.link.request(READ_MSG_CMD, trace=trace)
        data = decode_message_frame(response)
        self.rom_id = data["rom_id"]
        return data

    def read_standard_model(self, trace=True):
        response = self.link.request(MODEL_CMD, trace=trace)
        _require(response, 9, "model")
        model = bytes(bytearray(response[2:9])).decode("utf-8")
        self.command_version = ""
        self.model = model
        return model

    def read_f0513_model(self, trace=True):
        # Test mode for these batteries is handled by the adapter firmware,
        # which sends CC 99 before the 0x31/0x32 command.
        response = self.link.request(F0513_MODEL_CMD, trace=trace)
        _require(response, 4, "F0513 model")
        self.link.request(CLEAR_CMD, trace=trace)
        self.command_version = F0513
        self.model = "BL%X%X" % (response[2], response[3])
        return self.model

    def detect_model(self, trace=True):
        """Try the standard model command first, then the F0513 one.

        Returns ``(model, command_version)`` where ``command_version`` is
        ``""`` for standard packs and ``"F0513"`` for the limited ones.
        """
        last_error = None
        for reader in (self.read_standard_model, self.read_f0513_model):
            try:
                model = reader(trace=trace)
                return model, self.command_version
            except Exception as exc:
                last_error = exc
        raise ValueError("Battery is present but the model is not supported. "
                         "Last error: %s" % last_error)

    # ------------------------------------------------------------------
    def read_pack_data(self, trace=True):
        """Read voltages and temperatures using the detected command set."""
        if self.command_version == F0513:
            # These packs need the read pipeline cleared first; upstream sends
            # the clear command twice and that timing is load-bearing.
            self.link.request(CLEAR_CMD, trace=trace)
            self.link.request(CLEAR_CMD, trace=trace)
            cells = [self.link.request(cmd, trace=trace) for cmd in F0513_VCELL_CMDS]
            temp = self.link.request(F0513_TEMP_CMD, trace=trace)
            return decode_f0513_data(cells, temp)
        return decode_pack_data(self.link.request(READ_DATA_REQUEST, trace=trace))

    def sample(self, include_status=False, trace=False):
        """One flat reading for the logger.

        Static values (model, ROM id) come from the last detection so that a
        sample stays a single round of commands.
        """
        data = {"model": self.model or "", "rom_id": self.rom_id or ""}
        data.update(self.read_pack_data(trace=trace))
        if include_status:
            message = self.read_message(trace=trace)
            data.update({
                "state": message["state"],
                "status_code": message["status_code"],
                "charge_count": message["charge_count"],
            })
        return data
