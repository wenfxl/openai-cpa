import os
import tempfile
import unittest
from unittest.mock import patch

from utils.system_maintenance import get_cleanup_status


class SystemMaintenanceTests(unittest.TestCase):
    def test_get_cleanup_status_reports_script_presence_on_linux(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = os.path.join(tmpdir, "scripts")
            os.makedirs(scripts_dir, exist_ok=True)
            script_path = os.path.join(scripts_dir, "server_disk_cleanup.sh")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env bash\nexit 0\n")

            with patch("utils.system_maintenance.platform.system", return_value="Linux"):
                status = get_cleanup_status(tmpdir)

        self.assertTrue(status["is_linux"])
        self.assertTrue(status["script_exists"])
        self.assertTrue(status["can_run"])
        self.assertEqual(script_path, status["script_path"])


if __name__ == "__main__":
    unittest.main()
