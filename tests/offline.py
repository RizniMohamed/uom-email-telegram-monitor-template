import unittest
from unittest.mock import patch


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "requests.sessions.Session.request"):
            block = patch(target, side_effect=AssertionError("External network access in offline test"))
            block.start()
            self.addCleanup(block.stop)
