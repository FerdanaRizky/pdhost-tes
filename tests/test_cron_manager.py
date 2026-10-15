import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import agent


class CronManagerTests(unittest.TestCase):
    def test_rejects_invalid_domain_and_script_traversal(self):
        result = agent.create_cron_job({'domain':'bad_domain','script':'task.php'})
        self.assertFalse(result['ok'])
        result = agent.create_cron_job({'domain':'example.com','script':'../secret.php'})
        self.assertFalse(result['ok'])

    def test_rejects_out_of_range_cron_values(self):
        result = agent.create_cron_job({'domain':'example.com','script':'task.php','minute':'99'})
        self.assertFalse(result['ok'])

    def test_rejects_shell_metacharacters_in_schedule(self):
        result = agent.create_cron_job({'domain':'example.com','script':'tasks/job.php','minute':'*;id'})
        self.assertFalse(result['ok'])

    @patch.object(agent.shutil, 'which', return_value='/usr/bin/php')
    def test_create_uses_existing_file_and_managed_cron_file(self, which):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'example.com' / 'public'
            root.mkdir(parents=True)
            (root / 'task.php').write_text('<?php echo 1;', encoding='utf-8')
            with patch.object(agent, 'SITE_ROOT', tmp), patch.object(agent, 'CRON_DIR', tmp):
                result = agent.create_cron_job({'domain':'example.com','script':'task.php','minute':'0','hour':'3','day':'*','month':'*','weekday':'*'})
                self.assertTrue(result['ok'], result)
                cron = Path(tmp) / result['file']
                self.assertTrue(cron.exists())
                content = cron.read_text(encoding='utf-8')
                self.assertIn('/usr/bin/php', content)
                self.assertIn('www-data', content)
                self.assertNotIn(';', content)


if __name__ == '__main__':
    unittest.main()
