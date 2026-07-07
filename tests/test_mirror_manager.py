import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mirror import MirrorManager


class FakeMirrorSession:
    def __init__(self, running=True, clients=0):
        self.running = running
        self.clients = {str(i): object() for i in range(clients)}
        self.stopped = False

    async def stop(self):
        self.stopped = True
        self.running = False
        return True

    def snapshot(self):
        return {"running": self.running, "clients": len(self.clients)}


class MirrorManagerTests(unittest.TestCase):
    def test_stop_other_no_client_sessions_keeps_current_and_viewed_sessions(self):
        async def run():
            manager = MirrorManager()
            broadcasts = []

            async def broadcast(message):
                broadcasts.append(message)

            manager.broadcast = broadcast
            manager.sessions = {
                "keep": FakeMirrorSession(running=True, clients=0),
                "idle": FakeMirrorSession(running=True, clients=0),
                "viewed": FakeMirrorSession(running=True, clients=1),
                "denied": FakeMirrorSession(running=True, clients=0),
            }

            stopped = await manager.stop_other_no_client_sessions("keep", ["keep", "idle", "viewed"])

            self.assertEqual(stopped, ["idle"])
            self.assertFalse(manager.sessions["idle"].running)
            self.assertTrue(manager.sessions["idle"].stopped)
            self.assertTrue(manager.sessions["keep"].running)
            self.assertTrue(manager.sessions["viewed"].running)
            self.assertTrue(manager.sessions["denied"].running)
            self.assertEqual(len(broadcasts), 1)
            self.assertEqual(broadcasts[0]["reason"], "switch")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
