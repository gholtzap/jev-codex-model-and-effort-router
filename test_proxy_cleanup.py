"""Failure cases: unrelated Python processes, connected clients, and changed owners."""
import unittest
from pathlib import Path
from proxy_cleanup import candidates


class ProxyCleanupTests(unittest.TestCase):
    def test_only_abandoned_managed_proxies_are_candidates(self):
        managed = str(Path.home() / '.local/share/jev-codex/releases/test/native_proxy.py')
        rows = [(10, 1, ['/usr/bin/python3', managed, '--socket', '/tmp/a/route.sock']),
                (11, 2, ['/usr/bin/python3', managed, '--socket', '/tmp/b/route.sock']),
                (12, 1, ['/usr/bin/python3', '/tmp/native_proxy.py', '--socket', '/tmp/c/route.sock']),
                (13, 1, ['/usr/bin/python3', managed, '--socket', '/tmp/d/route.sock']),
                (14, 1, ['/bin/codex', '--remote', 'unix:///tmp/d/route.sock'])]
        self.assertEqual([row['pid'] for row in candidates(rows)], [10])


if __name__ == '__main__':
    unittest.main()
