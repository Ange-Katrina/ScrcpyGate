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
        cls.catalog = (ROOT / "static" / "i18n" / "zh-CN.json").read_text(encoding="utf-8")

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
        translations = r"""
const adminT = (key, values={}) => {
  const messages = {
    'mirror.quality.raw_size': '原始尺寸',
    'admin.video.size_long_edge': '{size} · 最长边 {edge}px',
  };
  return (messages[key] || key).replace(/\{([^{}]+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
  ));
};
"""
        program = f"{translations}\n{prelude}\n{definitions}\nconsole.log(JSON.stringify({expression}));"
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

    def test_custom_height_links_back_to_a_16_by_9_width(self):
        result = self.run_node(
            ("dimensionsFromHeight",),
            "[480,540,720,900,1080].map(value=>dimensionsFromHeight(value))",
            "const MIN_OUTPUT_SIZE=854; const MAX_OUTPUT_SIZE=1920; const MIN_OUTPUT_HEIGHT=480; const MAX_OUTPUT_HEIGHT=1080;",
        )
        self.assertEqual(
            [(item["width"], item["height"]) for item in result],
            [(854, 480), (960, 540), (1280, 720), (1600, 900), (1920, 1080)],
        )

        unstable = self.run_node(
            ("outputSizeMeta", "dimensionsFromHeight"),
            "Array.from({length:601},(_,index)=>index+480).filter(value=>{const item=dimensionsFromHeight(value);return outputSizeMeta(item.maxSize).height!==item.height})",
            """
const MIN_OUTPUT_SIZE=854; const MAX_OUTPUT_SIZE=1920; const MIN_OUTPUT_HEIGHT=480; const MAX_OUTPUT_HEIGHT=1080;
const STANDARD_OUTPUT_SIZES=[
  {maxSize:854,width:854,height:480,quality:'480p'}, {maxSize:960,width:960,height:540,quality:'540p'},
  {maxSize:1280,width:1280,height:720,quality:'720p'}, {maxSize:1600,width:1600,height:900,quality:'900p'},
  {maxSize:1920,width:1920,height:1080,quality:'1080p'}
];
""",
        )
        self.assertEqual(unstable, [])

    def test_size_selector_offers_standard_and_custom_resolutions(self):
        self.assertNotIn("输出尺寸下拉项按 16:9 显示完整参考值", self.template)
        self.assertNotIn("输出尺寸提供 854 × 480", self.template)
        for key in (
            "admin.video.size_480",
            "admin.video.size_540",
            "admin.video.size_720",
            "admin.video.size_900",
            "admin.video.size_1080",
        ):
            self.assertIn(f"t('{key}')", self.template)
        for label in (
            "\"size_480\": \"854 × 480（480p）\"",
            "\"size_540\": \"960 × 540（540p）\"",
            "\"size_720\": \"1280 × 720（720p）\"",
            "\"size_900\": \"1600 × 900（900p）\"",
            "\"size_1080\": \"1920 × 1080（1080p）\"",
        ):
            self.assertIn(label, self.catalog)
        self.assertIn("t('admin.video.custom_output_size')", self.template)
        self.assertIn('"custom_output_size": "自定义输出尺寸"', self.catalog)
        self.assertIn('id="customProfileWidth" type="number" min="854" max="1920"', self.template)
        self.assertIn('id="customProfileHeight" type="number" min="480" max="1080"', self.template)
        self.assertIn('aria-label="{{ t(\'admin.video.custom_output_size\') }}"', self.template)
        self.assertIn("const STANDARD_OUTPUT_SIZES = Object.freeze([", self.script)
        self.assertIn("function presetSizeControl(profile,value)", self.script)
        self.assertIn("function syncPresetSizeControl(control,value,forceCustom=false)", self.script)
        self.assertIn("function syncPresetCustomDimensions(control,source)", self.script)
        self.assertIn("width.dataset.customWidth='1'", self.script)
        self.assertIn("height.dataset.customHeight='1'", self.script)
        self.assertIn("!width.checkValidity() || !height.checkValidity()", self.script)
        self.assertIn("admin.video.custom_size_invalid", self.script)
        self.assertIn('"custom_size_invalid": "自定义宽度或高度超出允许范围"', self.catalog)
        self.assertIn(".preset-size-control", self.styles)
        self.assertIn(".custom-size-editor", self.styles)
        self.assertIn(".custom-size-pair", self.styles)
        self.assertIn(".custom-size-field input {\n    min-height: 44px;", self.styles)

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
const customWidth={value:'1000',checkValidity:()=>true,reportValidity:()=>{}};
const customHeight={value:'563',checkValidity:()=>true,reportValidity:()=>{}};
const customContainer={querySelector:selector=>selector.includes('width')?customWidth:customHeight};
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
        self.assertIn("t('admin.ui.video.presets_description')", self.template)
        self.assertIn("所有用户共用流畅、稳定、高清和低延迟四档", self.catalog)

    def test_fullscreen_quality_uses_only_720p_or_higher_profiles(self):
        self.assertIn('id="videoFullscreenProfile"', self.template)
        self.assertIn('id="videoFullscreenHint"', self.template)
        self.assertIn("t('admin.ui.video.fullscreen_hint')", self.template)
        self.assertIn("仅显示 720p 或更高的档位", self.catalog)
        self.assertIn("function renderFullscreenProfiles(data,selected)", self.script)
        self.assertIn("Number(profiles[name].max_size)>=minimum", self.script)
        self.assertIn("fullscreen_profile:fullscreenProfile", self.script)

    def test_bitrate_inputs_display_mbps_and_save_bps(self):
        result = self.run_node(
            ("bitrateBpsToMbps", "bitrateMbpsToBps"),
            "[450000,900000,5500000].map(value=>bitrateMbpsToBps(bitrateBpsToMbps(value)))",
        )
        self.assertEqual(result, [450000, 900000, 5500000])
        self.assertGreaterEqual(self.template.count("admin.ui.video.bitrate_mbps"), 3)
        self.assertIn('"bitrate_mbps": "码率 Mbps"', self.catalog)
        self.assertNotIn("码率 bps", self.template)
        self.assertIn('content: "码率 Mbps"', self.styles)


if __name__ == "__main__":
    unittest.main()
