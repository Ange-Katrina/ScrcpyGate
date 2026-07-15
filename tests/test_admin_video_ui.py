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

    def test_output_size_labels_use_complete_dimensions(self):
        result = self.run_node(
            ("outputSizeMeta", "outputSizeOptionLabel", "maxSizeQualityLabel"),
            "[854,960,1280,1600,1920,1000].map(maxSizeQualityLabel)",
            """
const MIN_OUTPUT_SIZE=854;
const MAX_OUTPUT_SIZE=1920;
const STANDARD_OUTPUT_SIZES=[
  {maxSize:854,width:854,height:480,quality:'480p'},
  {maxSize:960,width:960,height:540,quality:'540p'},
  {maxSize:1280,width:1280,height:720,quality:'720p'},
  {maxSize:1600,width:1600,height:900,quality:'900p'},
  {maxSize:1920,width:1920,height:1080,quality:'1080p'}
];
""",
        )
        self.assertEqual(
            result,
            [
                "854 × 480（480p） · 最长边 854px",
                "960 × 540（540p） · 最长边 960px",
                "1280 × 720（720p） · 最长边 1280px",
                "1600 × 900（900p） · 最长边 1600px",
                "1920 × 1080（1080p） · 最长边 1920px",
                "1000 × 563 · 最长边 1000px",
            ],
        )

    def test_size_selector_offers_standard_and_custom_resolutions(self):
        for label in (
            "854 × 480（480p）",
            "960 × 540（540p）",
            "1280 × 720（720p）",
            "1600 × 900（900p）",
            "1920 × 1080（1080p）",
            "自定义尺寸…",
        ):
            self.assertIn(label, self.template if label != "自定义尺寸…" else self.template + self.script)
        self.assertIn('id="customProfileSize" type="number" min="854" max="1920"', self.template)
        self.assertIn("const STANDARD_OUTPUT_SIZES = Object.freeze([", self.script)
        self.assertIn("function presetSizeControl(profile,value)", self.script)
        self.assertIn("function syncPresetSizeControl(control,value,forceCustom=false)", self.script)
        self.assertIn("!sizeInput.checkValidity()", self.script)
        self.assertIn("sizeInput.reportValidity()", self.script)
        self.assertIn("自定义最长边必须在 ${MIN_OUTPUT_SIZE}–${MAX_OUTPUT_SIZE} 之间", self.script)
        self.assertIn(".preset-size-control", self.styles)
        self.assertIn(".custom-size-editor", self.styles)

    def test_collect_video_presets_reads_standard_and_custom_size_controls(self):
        result = self.run_node(
            ("bitrateMbpsToBps", "outputSizeMeta", "collectVideoPresets"),
            "collectVideoPresets()",
            """
const MIN_OUTPUT_SIZE=854;
const MAX_OUTPUT_SIZE=1920;
const CUSTOM_OUTPUT_SIZE='custom';
const STANDARD_OUTPUT_SIZES=[
  {maxSize:854,width:854,height:480,quality:'480p'},
  {maxSize:960,width:960,height:540,quality:'540p'},
  {maxSize:1280,width:1280,height:720,quality:'720p'},
  {maxSize:1600,width:1600,height:900,quality:'900p'},
  {maxSize:1920,width:1920,height:1080,quality:'1080p'}
];
const customContainer={querySelector:()=>({value:'1000'})};
const inputs=[
  {dataset:{presetProfile:'smooth',presetField:'video_bit_rate'},value:'1.25'},
  {dataset:{presetProfile:'smooth',presetField:'max_size'},value:'854'},
  {dataset:{presetProfile:'smooth',presetField:'max_fps'},value:'24'},
  {dataset:{presetProfile:'sharp',presetField:'video_bit_rate'},value:'6'},
  {dataset:{presetProfile:'sharp',presetField:'max_size'},value:'custom',closest:()=>customContainer},
  {dataset:{presetProfile:'sharp',presetField:'max_fps'},value:'30'}
];
global.document={querySelectorAll:()=>inputs};
""",
        )
        self.assertEqual(result["smooth"], {"video_bit_rate": 1250000, "max_size": 854, "max_fps": 24})
        self.assertEqual(result["sharp"], {"video_bit_rate": 6000000, "max_size": 1000, "max_fps": 30})

    def test_admin_exposes_only_four_shared_profiles(self):
        self.assertIn("const NORMAL_PROFILE_NAMES = ['smooth','balanced','sharp','low_latency']", self.script)
        self.assertIn("NORMAL_PROFILE_NAMES.forEach(name=>", self.script)
        self.assertNotIn("ALAS_PROFILE_NAMES", self.script)
        self.assertNotIn("video_mode", self.script)
        self.assertNotIn("ALAS 专属", self.template)
        self.assertNotIn("画质类型", self.template)
        self.assertIn("所有用户共用流畅、稳定、高清和低延迟四档", self.template)

    def test_bitrate_inputs_display_mbps_and_save_bps(self):
        result = self.run_node(
            ("bitrateBpsToMbps", "bitrateMbpsToBps"),
            "[450000,900000,5500000].map(value=>bitrateMbpsToBps(bitrateBpsToMbps(value)))",
        )
        self.assertEqual(result, [450000, 900000, 5500000])
        self.assertGreaterEqual(self.template.count("码率 Mbps"), 3)
        self.assertNotIn("码率 bps", self.template)
        self.assertIn('content: "码率 Mbps"', self.styles)


if __name__ == "__main__":
    unittest.main()
