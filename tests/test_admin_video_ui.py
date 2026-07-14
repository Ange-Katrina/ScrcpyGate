import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminVideoUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")

    @classmethod
    def function_source(cls, name):
        prefix = f"function {name}("
        for line in cls.script.splitlines():
            if line.startswith(prefix):
                return line
        raise AssertionError(f"missing JavaScript function: {name}")

    @classmethod
    def run_node(cls, function_names, expression, prelude=""):
        definitions = "\n".join(cls.function_source(name) for name in function_names)
        program = f"{prelude}\n{definitions}\nconsole.log(JSON.stringify({expression}));"
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_bitrate_inputs_display_mbps_and_collect_bps(self):
        result = self.run_node(
            (
                "bitrateBpsToMbps",
                "bitrateMbpsToBps",
                "maxSizeQualityLabel",
                "presetFieldDisplayValue",
                "presetInput",
                "collectVideoPresets",
            ),
            """(()=>{
                const bitrate=presetInput('smooth','video_bit_rate',450000);
                const size=presetInput('smooth','max_size',720);
                const initial={value:bitrate.value,min:bitrate.min,max:bitrate.max,step:bitrate.step};
                bitrate.value='5.5';
                inputs.push(bitrate,size);
                return {initial,presets:collectVideoPresets()};
            })()""",
            "const inputs=[]; global.document={createElement:()=>({dataset:{}}),querySelectorAll:()=>inputs};",
        )

        self.assertEqual(result["initial"], {"value": 0.45, "min": "0.1", "max": "100", "step": "0.05"})
        self.assertEqual(result["presets"]["smooth"]["video_bit_rate"], 5500000)
        self.assertEqual(result["presets"]["smooth"]["max_size"], 720)

    def test_max_size_is_labeled_as_scrcpy_long_edge_and_equivalent_quality(self):
        result = self.run_node(
            ("maxSizeQualityLabel",),
            "[854,960,1280,1600,1920].map(maxSizeQualityLabel)",
        )
        self.assertEqual(result, [
            "480p · 长边 854px",
            "540p · 长边 960px",
            "720p · 长边 1280px",
            "900p · 长边 1600px",
            "1080p · 长边 1920px",
        ])
        self.assertIn("最长边 px", self.template)
        self.assertIn("1280≈720p", self.template)
        self.assertNotIn("推荐输出尺寸最低 720", self.template)

    def test_bitrate_conversion_round_trips_recommendation_values(self):
        result = self.run_node(
            ("bitrateBpsToMbps", "bitrateMbpsToBps"),
            "[450000,900000,5500000].map(value=>bitrateMbpsToBps(bitrateBpsToMbps(value)))",
        )
        self.assertEqual(result, [450000, 900000, 5500000])

    def test_admin_labels_and_custom_profiles_use_mbps_at_the_ui_boundary(self):
        self.assertGreaterEqual(self.template.count("码率 Mbps"), 3)
        self.assertNotIn("码率 bps", self.template)
        self.assertIn('id="customProfileBitrate" type="number" min="0.1" max="100" step="0.05"', self.template)
        self.assertIn('value="0.9"', self.template)
        self.assertIn('content: "码率 Mbps"', self.styles)
        self.assertNotIn('content: "码率 bps"', self.styles)
        self.assertIn('content: "最长边 px"', self.styles)
        self.assertIn("td(bitrateBpsToMbps(profile.video_bit_rate))", self.script)
        self.assertIn("video_bit_rate:bitrateMbpsToBps($('customProfileBitrate').value || 0.9)", self.script)
        self.assertIn("input.value=presetFieldDisplayValue(field, value)", self.script)

    def test_admin_exposes_four_normal_and_four_alas_presets(self):
        self.assertIn("const ALL_PROFILE_NAMES = NORMAL_PROFILE_NAMES.concat(ALAS_PROFILE_NAMES)", self.script)
        self.assertIn("ALL_PROFILE_NAMES.forEach(name=>", self.script)
        self.assertIn("const group=profileGroup==='alas' ? 'ALAS · ' : '普通 · '", self.script)
        self.assertIn('tr.dataset.profileGroup=profileGroup', self.script)
        self.assertIn('tr[data-profile-group="normal"] + tr[data-profile-group="alas"]', self.styles)
        self.assertIn('tr[data-profile-group="normal"] + tr[data-profile-group="alas"] td {\n    border-top: 0;', self.styles)
        self.assertIn("普通与 ALAS 两组画质，每组都有流畅、稳定、高清、低延迟四档", self.template)


if __name__ == "__main__":
    unittest.main()
