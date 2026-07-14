import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.video_options import (
    BANDWIDTH_RECOMMENDATIONS,
    VideoOptionError,
    enabled_stream_modes_value,
    normalize_custom_profile_payloads,
    normalize_profile_payloads,
    normalize_video_options,
    profile_label_payloads,
    profile_payloads,
    serialize_custom_profiles,
    settings_to_video_options,
)


class VideoOptionsTests(unittest.TestCase):
    def test_request_payload_overrides_user_and_default_values(self):
        defaults = {"profile": "balanced", "adaptive": False, "video_bit_rate": 900000, "max_size": 540, "max_fps": 24, "scrcpy_stream_mode": "raw"}
        user_pref = normalize_video_options(
            {"profile": "custom", "adaptive": True, "video_bit_rate": 2500000, "max_size": 960, "max_fps": 24, "scrcpy_stream_mode": "protocol"},
            defaults,
        )
        request = normalize_video_options({"video_bit_rate": 1500000, "max_size": 720, "max_fps": 24}, user_pref)

        self.assertEqual(request["video_bit_rate"], 1500000)
        self.assertEqual(request["max_size"], 720)
        self.assertEqual(request["max_fps"], 24)
        self.assertEqual(request["profile"], "custom")
        self.assertEqual(request["scrcpy_stream_mode"], "protocol")

    def test_admin_settings_are_valid_defaults(self):
        options = settings_to_video_options({"video_profile": "balanced", "video_bit_rate": "900000", "max_size": "540", "max_fps": "24"})

        self.assertEqual(options["video_bit_rate"], 900000)
        self.assertEqual(options["max_size"], 540)
        self.assertEqual(options["max_fps"], 24)
        self.assertEqual(options["profile"], "balanced")
        self.assertEqual(options["scrcpy_stream_mode"], "raw")

    def test_disabled_stream_mode_falls_back_to_raw(self):
        enabled = enabled_stream_modes_value("raw")
        options = normalize_video_options({"scrcpy_stream_mode": "protocol"}, enabled_stream_modes=enabled)

        self.assertEqual(options["scrcpy_stream_mode"], "raw")

    def test_protocol_stream_mode_requires_enable_switch(self):
        enabled = enabled_stream_modes_value("raw,protocol")
        options = normalize_video_options({"scrcpy_stream_mode": "protocol"}, enabled_stream_modes=enabled)

        self.assertEqual(options["scrcpy_stream_mode"], "protocol")

    def test_admin_can_override_profile_presets(self):
        profiles = profile_payloads({
            "video_preset_balanced_video_bit_rate": "1100000",
            "video_preset_balanced_max_size": "480",
            "video_preset_balanced_max_fps": "30",
        })
        options = normalize_video_options({"profile": "balanced"}, profiles=profiles)

        self.assertEqual(options["video_bit_rate"], 1100000)
        self.assertEqual(options["max_size"], 480)
        self.assertEqual(options["max_fps"], 30)

    def test_profile_presets_reject_too_small_output(self):
        with self.assertRaises(VideoOptionError):
            normalize_profile_payloads({"smooth": {"max_size": 360}})

    def test_custom_profiles_are_available_to_normalizer(self):
        custom = normalize_custom_profile_payloads({
            "office_5m": {"label": "鍔炲叕瀹?M", "video_bit_rate": 1400000, "max_size": 720, "max_fps": 24}
        })
        settings = {"video_custom_profiles": serialize_custom_profiles(custom)}
        profiles = profile_payloads(settings)
        labels = profile_label_payloads(settings)
        options = normalize_video_options({"profile": "office_5m"}, profiles=profiles)

        self.assertEqual(labels["office_5m"], "鍔炲叕瀹?M")
        self.assertEqual(options["video_bit_rate"], 1400000)
        self.assertEqual(options["max_size"], 720)
        self.assertEqual(options["max_fps"], 24)

    def test_custom_profiles_reject_reserved_or_too_small_values(self):
        with self.assertRaises(VideoOptionError):
            normalize_custom_profile_payloads({"smooth": {"max_size": 480}})
        with self.assertRaises(VideoOptionError):
            normalize_custom_profile_payloads({"office": {"max_size": 360}})

        custom = normalize_custom_profile_payloads({
            "office": {"video_bit_rate": 900000, "max_size": 480, "max_fps": 24}
        })
        self.assertEqual(custom["office"]["max_size"], 480)

    def test_bandwidth_recommendations_include_expected_levels(self):
        self.assertEqual(set(BANDWIDTH_RECOMMENDATIONS), {"2mbps", "5mbps", "10mbps", "20mbps"})
        for level, profiles in BANDWIDTH_RECOMMENDATIONS.items():
            for profile, values in profiles.items():
                with self.subTest(level=level, profile=profile):
                    self.assertGreaterEqual(values["max_size"], 720)
        self.assertEqual(BANDWIDTH_RECOMMENDATIONS["10mbps"]["sharp"]["max_size"], 960)
        self.assertEqual(BANDWIDTH_RECOMMENDATIONS["20mbps"]["sharp"]["max_size"], 1280)

    def test_invalid_ranges_are_rejected(self):
        with self.assertRaises(VideoOptionError):
            normalize_video_options({"max_size": -1})
        with self.assertRaises(VideoOptionError):
            normalize_video_options({"max_size": 360})
        with self.assertRaises(VideoOptionError):
            normalize_video_options({"max_fps": 241})
        with self.assertRaises(VideoOptionError):
            normalize_video_options({"video_bit_rate": "bad"})
        with self.assertRaises(VideoOptionError):
            normalize_video_options({"scrcpy_stream_mode": "auto"})


if __name__ == "__main__":
    unittest.main()
