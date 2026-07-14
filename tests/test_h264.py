import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.h264 import (
    H264AnnexBParser,
    H264_NAL_IDR,
    H264_NAL_PPS,
    H264_NAL_SPS,
    annexb_nal_types,
    first_mb_in_slice,
    is_first_vcl_nal,
    normalize_h264_payload,
    nal_type,
)


class H264ParserTests(unittest.TestCase):
    def test_parser_emits_complete_nals_across_chunks(self):
        parser = H264AnnexBParser()
        sps = b"\x00\x00\x00\x01\x67\x42\x00\x1f"
        pps = b"\x00\x00\x01\x68\xce\x06"
        idr = b"\x00\x00\x00\x01\x65\x88\x84"

        self.assertEqual(parser.feed(sps[:3]), [])
        self.assertEqual(parser.feed(sps[3:] + pps[:3]), [sps])
        self.assertEqual(parser.feed(pps[3:] + idr[:4]), [pps])
        self.assertEqual(parser.feed(idr[4:] + b"\x00\x00\x01\x41\x9a"), [idr])

    def test_nal_type_handles_three_and_four_byte_start_codes(self):
        self.assertEqual(nal_type(b"\x00\x00\x00\x01\x67\x42"), H264_NAL_SPS)
        self.assertEqual(nal_type(b"\x00\x00\x01\x68\xce"), H264_NAL_PPS)
        self.assertEqual(nal_type(b"\x00\x00\x00\x01\x65\x88"), H264_NAL_IDR)

    def test_normalize_length_prefixed_payload_to_annexb(self):
        sps = b"\x67\x42\x00\x1f"
        idr = b"\x65\x88\x84"
        payload = len(sps).to_bytes(4, "big") + sps + len(idr).to_bytes(4, "big") + idr

        normalized = normalize_h264_payload(payload)

        self.assertEqual(normalized, b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + idr)
        self.assertEqual(annexb_nal_types(normalized), [H264_NAL_SPS, H264_NAL_IDR])

    def test_normalize_avcc_config_to_annexb(self):
        sps = b"\x67\x42\x00\x1f"
        pps = b"\x68\xce"
        avcc = (
            b"\x01\x42\x00\x1f\xff"
            + b"\xe1"
            + len(sps).to_bytes(2, "big")
            + sps
            + b"\x01"
            + len(pps).to_bytes(2, "big")
            + pps
        )

        normalized = normalize_h264_payload(avcc)

        self.assertEqual(normalized, b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + pps)
        self.assertEqual(annexb_nal_types(normalized), [H264_NAL_SPS, H264_NAL_PPS])

    def test_first_mb_in_slice_distinguishes_multi_slice_frames(self):
        first_slice = b"\x00\x00\x00\x01\x65\x80"
        later_slice = b"\x00\x00\x00\x01\x65\x40"

        self.assertEqual(first_mb_in_slice(first_slice), 0)
        self.assertEqual(first_mb_in_slice(later_slice), 1)
        self.assertTrue(is_first_vcl_nal(first_slice))
        self.assertFalse(is_first_vcl_nal(later_slice))
        self.assertIsNone(first_mb_in_slice(b"\x00\x00\x00\x01\x67\x80"))


if __name__ == "__main__":
    unittest.main()
