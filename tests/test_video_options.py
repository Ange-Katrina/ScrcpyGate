import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.video_options import (
    ALAS_PROFILE_MAX_SIZE,
    ALAS_PROFILE_NAMES,
    BANDWIDTH_RECOMMENDATIONS,
    NORMAL_PROFILE_NAMES,
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
        options = settings_to_video_options({"video_profile": "balanced", "video_bit_rate": "2400000", "max_size": "1280", "max_fps": "24"})

        self.assertEqual(options["video_bit_rate"], 2400000)
        self.assertEqual(options["max_size"], 1280)
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
        with self.assertRaises(VideoOptionError):
            normalize_custom_profile_payloads({"alas_smooth": {"max_size": 960}})

        custom = normalize_custom_profile_payloads({
            "office": {"video_bit_rate": 900000, "max_size": 480, "max_fps": 24}
        })
        self.assertEqual(custom["office"]["max_size"], 480)

    def test_bandwidth_recommendations_include_expected_levels(self):
        self.assertEqual(set(BANDWIDTH_RECOMMENDATIONS), {"2mbps", "5mbps", "10mbps", "20mbps"})
        for level, profiles in BANDWIDTH_RECOMMENDATIONS.items():
            normal_sizes = [profiles[name]["max_size"] for name in NORMAL_PROFILE_NAMES]
            alas_sizes = [profiles[name]["max_size"] for name in ALAS_PROFILE_NAMES]
            with self.subTest(level=level):
                self.assertEqual(set(profiles), set(NORMAL_PROFILE_NAMES + ALAS_PROFILE_NAMES))
                self.assertTrue(any(size >= 1280 for size in normal_sizes), "each normal tier needs a real 720p option")
                self.assertNotEqual(set(normal_sizes), {1280}, "normal profiles must not all collapse to 720p")
                self.assertTrue(any(size >= 1280 for size in alas_sizes), "each ALAS tier needs a real 720p option")
                self.assertTrue(all(size <= 1280 for size in alas_sizes), "ALAS profiles must stay at or below 720p")
                self.assertNotEqual(set(alas_sizes), {1280}, "ALAS profiles must preserve lower-bandwidth choices")
                bandwidth = int(level.removesuffix("mbps")) * 1000000
                self.assertLessEqual(max(values["video_bit_rate"] for values in profiles.values()), bandwidth / 1.5)
        self.assertEqual(BANDWIDTH_RECOMMENDATIONS["2mbps"]["sharp"], {"video_bit_rate": 1300000, "max_size": 1280, "max_fps": 24})
        self.assertEqual(BANDWIDTH_RECOMMENDATIONS["10mbps"]["sharp"]["max_size"], 1920)
        self.assertEqual(BANDWIDTH_RECOMMENDATIONS["20mbps"]["balanced"]["max_size"], 1600)
        self.assertEqual(BANDWIDTH_RECOMMENDATIONS["20mbps"]["sharp"]["video_bit_rate"], 8500000)

    def test_alas_default_profiles_are_four_distinct_720p_capped_choices(self):
        profiles = profile_payloads()
        self.assertEqual(len(ALAS_PROFILE_NAMES), 4)
        self.assertTrue(all(profiles[name]["max_size"] <= ALAS_PROFILE_MAX_SIZE for name in ALAS_PROFILE_NAMES))
        self.assertEqual(profiles["alas_sharp"]["max_size"], ALAS_PROFILE_MAX_SIZE)

        stored = profile_payloads({"video_preset_alas_sharp_max_size": "1920"})
        self.assertEqual(stored["alas_sharp"]["max_size"], ALAS_PROFILE_MAX_SIZE)

        with self.assertRaisesRegex(VideoOptionError, "alas_sharp max_size must be between 480 and 1280"):
            normalize_profile_payloads({"alas_sharp": {"max_size": 1920}})
        with self.assertRaisesRegex(VideoOptionError, "alas_sharp max_size must be between 480 and 1280"):
            normalize_profile_payloads({"alas_sharp": {"max_size": 0}})

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
