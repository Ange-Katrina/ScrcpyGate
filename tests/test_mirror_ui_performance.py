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


if __name__ == "__main__":
    unittest.main()
