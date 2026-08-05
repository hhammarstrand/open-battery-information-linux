"""Frame decoding tests. No hardware, no display, no battery required."""

import unittest

from core import makita


def message_frame(**overrides):
    """Build a plausible 42 byte answer to READ_MSG_CMD."""
    frame = bytearray(42)
    frame[0] = 0x33          # echoed command
    frame[1] = 0x28          # payload length
    frame[2] = 0x24          # year  -> 2036 unless overridden
    frame[3] = 0x07          # month
    frame[4] = 0x0F          # day
    frame[5:10] = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
    frame[21] = 0x05         # battery type, nibble swapped -> 0x50
    frame[26] = 0x05         # capacity, nibble swapped -> 0x50 = 80 -> 8.0 Ah
    frame[29] = 0x1B         # status code
    frame[30] = 0x00         # lock nibble -> unlocked
    frame[36] = 0x21         # charge count low  (nibble swap -> 0x12)
    frame[37] = 0x43         # charge count high (nibble swap -> 0x34)
    for index, value in overrides.items():
        frame[int(index.lstrip("b"))] = value
    return bytes(frame)


class DecodeMessageFrameTest(unittest.TestCase):
    def test_decodes_identity_fields(self):
        data = makita.decode_message_frame(message_frame())

        self.assertEqual(data["rom_id"], "24 07 0F AA BB CC DD EE")
        self.assertEqual(data["manufacturing_date"], "15/07/2036")
        self.assertEqual(data["capacity_ah"], 8.0)
        self.assertEqual(data["battery_type"], 0x50)
        self.assertEqual(data["status_code"], "1B")
        self.assertEqual(len(data["battery_message"].split()), 32)

    def test_charge_count_is_nibble_swapped_and_masked(self):
        # bytes 37,36 -> nibble swap -> 0x34,0x12 -> reversed -> 0x1234,
        # masked with 0x0FFF -> 0x234 = 564
        data = makita.decode_message_frame(message_frame())
        self.assertEqual(data["charge_count"], 0x234)

    def test_lock_nibble_marks_locked_pack(self):
        unlocked = makita.decode_message_frame(message_frame(b30=0x00))
        locked = makita.decode_message_frame(message_frame(b30=0x01))
        upper_nibble_only = makita.decode_message_frame(message_frame(b30=0xA0))

        self.assertFalse(unlocked["locked"])
        self.assertEqual(unlocked["state"], "UNLOCKED")
        self.assertTrue(locked["locked"])
        self.assertEqual(locked["state"], "LOCKED")
        # Only the low nibble means "locked".
        self.assertFalse(upper_nibble_only["locked"])

    def test_short_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            makita.decode_message_frame(message_frame()[:20])


def pack_frame(pack_mv=18000, cells_mv=(3600, 3610, 3590, 3605, 3595),
               t1_c=2350, t2_c=2900):
    """Build a plausible answer to READ_DATA_REQUEST."""
    frame = bytearray(20)
    frame[0:2] = bytes([0xCC, 0x1D])
    frame[2:4] = pack_mv.to_bytes(2, "little")
    for index, millivolts in enumerate(cells_mv):
        frame[4 + 2 * index:6 + 2 * index] = millivolts.to_bytes(2, "little")
    frame[16:18] = t1_c.to_bytes(2, "little")
    frame[18:20] = t2_c.to_bytes(2, "little")
    return bytes(frame)


class DecodePackDataTest(unittest.TestCase):
    def test_decodes_voltages_and_temperatures(self):
        data = makita.decode_pack_data(pack_frame())

        self.assertEqual(data["pack_v"], 18.0)
        self.assertEqual(data["cell1_v"], 3.6)
        self.assertEqual(data["cell3_v"], 3.59)
        self.assertEqual(data["temp1_c"], 23.5)
        self.assertEqual(data["temp2_c"], 29.0)

    def test_cell_delta_uses_millivolt_resolution(self):
        data = makita.decode_pack_data(pack_frame(cells_mv=(3600, 3610, 3590, 3605, 3595)))
        self.assertEqual(data["cell_delta_v"], 0.02)

        tight = makita.decode_pack_data(pack_frame(cells_mv=(3600, 3601, 3600, 3600, 3600)))
        self.assertEqual(tight["cell_delta_v"], 0.001)

    def test_truncated_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            makita.decode_pack_data(pack_frame()[:10])


class DecodeF0513Test(unittest.TestCase):
    def cell(self, millivolts):
        return bytes(bytearray([0xCC, 0x02]) + millivolts.to_bytes(2, "little"))

    def test_pack_voltage_is_the_sum_of_cells(self):
        cells = [self.cell(mv) for mv in (3600, 3600, 3600, 3600, 3600)]
        data = makita.decode_f0513_data(cells, self.cell(2400))

        self.assertEqual(data["pack_v"], 18.0)
        self.assertEqual(data["cell5_v"], 3.6)
        self.assertEqual(data["cell_delta_v"], 0.0)
        self.assertEqual(data["temp1_c"], 24.0)
        self.assertIsNone(data["temp2_c"])

    def test_wrong_number_of_cells_is_rejected(self):
        with self.assertRaises(ValueError):
            makita.decode_f0513_data([self.cell(3600)], self.cell(2400))


class NibbleSwapTest(unittest.TestCase):
    def test_swaps_nibbles(self):
        self.assertEqual(makita.nibble_swap(0x12), 0x21)
        self.assertEqual(makita.nibble_swap(0x00), 0x00)
        self.assertEqual(makita.nibble_swap(0xF0), 0x0F)


class LogFieldnamesTest(unittest.TestCase):
    def test_status_columns_are_opt_in(self):
        plain = makita.log_fieldnames()
        with_status = makita.log_fieldnames(include_status=True)

        self.assertNotIn("charge_count", plain)
        self.assertIn("charge_count", with_status)
        self.assertEqual(with_status[:len(plain)], plain)


class FakeLink(object):
    """Replays canned responses and records the frames it was asked to send."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def request(self, frame, max_attempts=2, trace=True):
        self.sent.append(list(frame))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MakitaClientTest(unittest.TestCase):
    def test_detect_model_falls_back_to_f0513(self):
        f0513_model = bytes(bytearray([0x31, 0x02, 0x18, 0x05]))
        link = FakeLink([
            ConnectionError("no answer"),   # standard MODEL_CMD fails
            f0513_model,                    # F0513_MODEL_CMD answers
            None,                           # CLEAR_CMD
        ])
        client = makita.MakitaClient(link)

        model, version = client.detect_model()

        self.assertEqual(version, makita.F0513)
        self.assertEqual(model, "BL185")
        self.assertEqual(link.sent[0], makita.MODEL_CMD)
        self.assertEqual(link.sent[1], makita.F0513_MODEL_CMD)

    def test_detect_model_prefers_the_standard_command(self):
        response = bytes(bytearray([0xCC, 0x10]) + b"BL1850B" + bytes(9))
        client = makita.MakitaClient(FakeLink([response]))

        model, version = client.detect_model()

        self.assertEqual(model, "BL1850B")
        self.assertEqual(version, "")

    def test_detect_model_reports_when_nothing_works(self):
        client = makita.MakitaClient(FakeLink([
            ConnectionError("nope"), ConnectionError("also nope")]))

        with self.assertRaises(ValueError):
            client.detect_model()

    def test_sample_includes_cached_identity(self):
        link = FakeLink([pack_frame()])
        client = makita.MakitaClient(link)
        client.command_version = ""
        client.model = "BL1850B"
        client.rom_id = "01 02"

        row = client.sample()

        self.assertEqual(row["model"], "BL1850B")
        self.assertEqual(row["rom_id"], "01 02")
        self.assertEqual(row["pack_v"], 18.0)
        self.assertEqual(link.sent, [makita.READ_DATA_REQUEST])

    def test_f0513_sample_clears_before_reading_cells(self):
        cells = [bytes(bytearray([0xCC, 0x02]) + (3600).to_bytes(2, "little"))
                 for _ in range(5)]
        link = FakeLink([None, None] + cells + [
            bytes(bytearray([0xCC, 0x02]) + (2400).to_bytes(2, "little"))])
        client = makita.MakitaClient(link)
        client.command_version = makita.F0513

        row = client.sample()

        self.assertEqual(link.sent[0], makita.CLEAR_CMD)
        self.assertEqual(link.sent[1], makita.CLEAR_CMD)
        self.assertEqual(link.sent[2], makita.F0513_VCELL_1_CMD)
        self.assertEqual(row["pack_v"], 18.0)


if __name__ == "__main__":
    unittest.main()
