import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MirrorUiPerformanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "static/js/mirror.js").read_text(encoding="utf-8")

    def test_input_instances_are_destroyed_at_every_channel_boundary(self):
        self.assertIn("function destroyInput()", self.script)
        self.assertIn("input.destroy()", self.script)
        self.assertIn("function closeControlSocket()", self.script)
        self.assertIn("destroyInput();\n  if (ws)", self.script)
        self.assertIn("setControlOwnership(false); destroyInput(); render();", self.script)
        self.assertIn("function setupInput(){\n  const video=$('phoneVideo');\n  destroyInput();", self.script)
        self.assertIn("window.addEventListener('pagehide'", self.script)

    def test_resource_loading_has_abort_sequence_and_single_flight_guards(self):
        self.assertIn("const resourceRequests = Object.create(null);", self.script)
        self.assertIn("if (slot.promise && !force) return slot.promise;", self.script)
        self.assertIn("if (force && slot.controller) slot.controller.abort();", self.script)
        self.assertIn("const controller = new AbortController();", self.script)
        self.assertIn("if (sequence !== slot.sequence) return undefined;", self.script)
        self.assertIn("const requestRevision=state.sessionRevision;", self.script)
        self.assertIn("if (eventRevision > requestRevision", self.script)
        self.assertIn("applySessionSnapshot(state.devices", self.script)
        self.assertIn("Promise.allSettled([", self.script)
        self.assertLess(
            self.script.index("loadUser(force)"),
            self.script.index("loadDevices(force)"),
        )

    def test_event_socket_drives_updates_and_polling_is_visibility_aware(self):
        self.assertIn("function applyEventMessage(msg)", self.script)
        self.assertIn("if (msg.session) updateRealtimeSession(id, msg.session)", self.script)
        self.assertIn("session.control_lock=msg.lock || null", self.script)
        self.assertIn("if (document.hidden || !state.eventConnected", self.script)
        self.assertIn("}, 60000);", self.script)
        self.assertIn("}, 5000);", self.script)
        self.assertIn("document.addEventListener('visibilitychange'", self.script)
        self.assertNotIn("setInterval(()=>loadAll", self.script)
        self.assertNotIn("function scheduleRefresh()", self.script)

    def test_keyed_nodes_and_one_frame_layout_updates_are_used(self):
        self.assertIn("deviceNodes:new Map()", self.script)
        self.assertIn("qualityNodes:new Map()", self.script)
        self.assertIn("state.deviceNodes.get(id)", self.script)
        self.assertIn("if (btn !== cursor) box.insertBefore(btn, cursor);", self.script)
        self.assertNotIn("const box=$('devices'); box.textContent=''", self.script)
        self.assertIn("function scheduleLayout()", self.script)
        self.assertIn("state.layoutFrame=requestAnimationFrame", self.script)
        self.assertIn("new ResizeObserver(handleViewportResize)", self.script)

    def test_raw_player_groups_slices_and_recovers_missing_frames(self):
        self.assertIn("onMissingVideoFrames:()=>requestVideoKeyframe(playerSeq)", self.script)
        self.assertIn("if(state.playerSeq!==playerSeq) return;", self.script)
        self.assertIn("state.jmuxer.feed({video:completeAnnexBChunk(buf)});", self.script)
        self.assertNotIn("duration:frameDurationMs()", self.script)
        self.assertNotIn("last_keyframe_payload", self.script)

    def test_stale_player_callbacks_cannot_reset_the_current_player(self):
        start = self.script.index("function sendPlayerReset()")
        end = self.script.index("function trimPlaybackDelay", start)
        helpers = self.script[start:end]
        program = f"""
const scheduled=[];
globalThis.setTimeout=(callback)=>{{ scheduled.push(callback); return scheduled.length; }};
globalThis.clearTimeout=()=>{{}};
const sent=[];
const video={{pause:()=>{{}},removeAttribute:()=>{{}},load:()=>{{}}}};
const state={{
  videoWs:{{readyState:1,send:message=>sent.push(JSON.parse(message))}},
  jmuxer:null,
  playerSeq:0,
  playerResetTimer:null,
  lastPlayerResetAt:0,
  lastKeyframeRequestAt:0,
}};
const WebSocket={{OPEN:1}};
const $=()=>video;
const qualityPayload=()=>({{max_fps:24}});
class JMuxer {{
  constructor(options){{ this.options=options; this.destroyed=0; }}
  destroy(){{ this.destroyed+=1; }}
}}
{helpers}
recreateVideoPlayer(video);
const stale=state.jmuxer;
recreateVideoPlayer(video);
const current=state.jmuxer;
stale.options.onError(new Error('stale'));
stale.options.onMissingVideoFrames();
const afterStale={{scheduled:scheduled.length,sent:sent.length,currentDestroyed:current.destroyed}};
current.options.onMissingVideoFrames();
const afterMissing={{scheduled:scheduled.length,sent:sent.length,currentDestroyed:current.destroyed}};
state.lastPlayerResetAt=0;
current.options.onError(new Error('current'));
scheduled.shift()();
console.log(JSON.stringify({{
  afterStale,
  afterMissing,
  afterCurrent:{{sent:sent.length,currentDestroyed:current.destroyed,replaced:state.jmuxer!==current}}
}}));
"""
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["afterStale"], {"scheduled": 0, "sent": 0, "currentDestroyed": 0})
        self.assertEqual(result["afterMissing"], {"scheduled": 0, "sent": 1, "currentDestroyed": 0})
        self.assertEqual(result["afterCurrent"], {"sent": 2, "currentDestroyed": 1, "replaced": True})

    def test_player_rebuilds_when_h264_sps_changes_after_rotation(self):
        start = self.script.index("function annexBNalUnit")
        end = self.script.index("function completeAnnexBChunk", start)
        helpers = self.script[start:end]
        program = f"""
const state={{videoSpsSignature:''}};
{helpers}
const spsA=new Uint8Array([0,0,0,1,0x67,0x64,0x00,0x1f,0xaa,0,0,1,0x68,0xee]);
const nonSps=new Uint8Array([0,0,0,1,0x65,0x88]);
const spsB=new Uint8Array([0,0,1,0x67,0x64,0x00,0x1f,0xbb]);
const audThenSps=new Uint8Array([0,0,1,0x09,0xf0,0,0,1,0x67,0x42,0x00,0x1e]);
const result=[
  videoConfigurationChanged(spsA),
  videoConfigurationChanged(spsA),
  videoConfigurationChanged(nonSps),
  videoConfigurationChanged(spsB),
  videoConfigurationChanged(spsB),
];
console.log(JSON.stringify({{result,sps:Array.from(annexBNalUnit(spsA,7)),spsAfterAud:Array.from(annexBNalUnit(audThenSps,7)),signature:state.videoSpsSignature}}));
"""
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["result"], [False, False, False, True, False])
        self.assertEqual(result["sps"], [0x67, 0x64, 0x00, 0x1F, 0xAA])
        self.assertEqual(result["spsAfterAud"], [0x67, 0x42, 0x00, 0x1E])
        self.assertTrue(result["signature"])
        self.assertIn("function recreateVideoPlayer(video)", self.script)
        self.assertIn("const configurationChanged=videoConfigurationChanged(buf);", self.script)
        self.assertIn("if(configurationChanged){ state.videoReconfiguring=true; recreateVideoPlayer(video); }", self.script)
        rebuild = self.script.index("if(configurationChanged){ state.videoReconfiguring=true; recreateVideoPlayer(video); }")
        feed = self.script.index("state.jmuxer.feed({video:completeAnnexBChunk(buf)});")
        self.assertLess(
            rebuild,
            feed,
        )
        self.assertNotIn("if(configurationChanged) sendPlayerReset();", self.script)
        self.assertIn("if(msg && msg.type==='stream_reset')", self.script)
        self.assertIn("if(generation>state.streamGeneration)", self.script)
        self.assertIn("video.onresize = () => updateInputSize();", self.script)
        self.assertIn("if(metadataW>0 && metadataH>0) state.videoReconfiguring=false;", self.script)
        self.assertIn("if (state.videoReconfiguring && isTouch) return;", self.script)
        quality_start = self.script.index("async function saveOrApplyQuality()")
        quality_end = self.script.index("function closeVideoSocket()", quality_start)
        self.assertNotIn("reconnectSockets(", self.script[quality_start:quality_end])

        update_start = self.script.index("function updateInputSize()")
        update_end = self.script.index("function layoutVideo()", update_start)
        update_source = self.script[update_start:update_end]
        layout_program = f"""
const video={{videoWidth:1280,videoHeight:720}};
const resizeCalls=[];
const state={{screen:{{w:720,h:1280}},videoReconfiguring:true,input:{{resizeScreen:(w,h)=>resizeCalls.push([w,h])}}}};
const $=()=>video;
function scheduleLayout(){{ resizeCalls.push('layout'); }}
{update_source}
updateInputSize();
console.log(JSON.stringify({{screen:state.screen,reconfiguring:state.videoReconfiguring,resizeCalls}}));
"""
        layout_completed = subprocess.run(
            ["node", "-e", layout_program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        layout_result = json.loads(layout_completed.stdout)
        self.assertEqual(layout_result["screen"], {"w": 1280, "h": 720})
        self.assertFalse(layout_result["reconfiguring"])
        self.assertEqual(layout_result["resizeCalls"], [[1280, 720], "layout"])

    def test_video_and_control_reconnects_are_bounded(self):
        self.assertIn("const RECONNECT_DELAYS=[500, 1000, 2000, 4000, 8000]", self.script)
        self.assertIn("function scheduleVideoReconnect(id)", self.script)
        self.assertIn("function scheduleControlReconnect(id)", self.script)
        self.assertIn("attempt >= RECONNECT_DELAYS.length", self.script)
        self.assertIn("openVideo(id, {force:true, phase:'reconnecting', reconnect:true})", self.script)
        self.assertIn("openControl(id, {force:true, reconnect:true})", self.script)
        self.assertIn("function pauseReconnectTimers()", self.script)
        self.assertIn("state.resumeDeviceId=state.activeDeviceId", self.script)
        self.assertIn("reconnectSockets(resumeDeviceId, 'reconnecting')", self.script)


if __name__ == "__main__":
    unittest.main()
