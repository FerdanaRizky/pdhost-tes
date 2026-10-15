import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import agent


class DatabaseManagerTests(unittest.TestCase):
    @patch.object(agent, 'mysql_query')
    @patch.object(agent.shutil, 'which')
    def test_create_database_generates_scoped_account(self, which, mysql_query):
        which.side_effect = lambda name: '/usr/bin/mariadb' if name == 'mariadb' else None
        result = agent.create_database()
        self.assertTrue(result['ok'], result)
        self.assertRegex(result['database'], r'^oh_[a-f0-9]{10}$')
        self.assertRegex(result['username'], r'^ohu_[a-f0-9]{10}$')
        self.assertGreaterEqual(len(result['password']), 24)
        sql = mysql_query.call_args.args[0]
        self.assertIn('GRANT ALL PRIVILEGES ON `' + result['database'] + '`.*', sql)
        self.assertNotIn('GRANT ALL PRIVILEGES ON *.*', sql)
        self.assertIn("'localhost'", sql)

    @patch.object(agent.shutil, 'which', return_value=None)
    def test_create_fails_when_client_missing(self, which):
        result = agent.create_database()
        self.assertFalse(result['ok'])

    @patch.object(agent, 'mysql_query', return_value='information_schema\nmysql\noh_0123456789\notherdb\n')
    @patch.object(agent.shutil, 'which')
    def test_lists_only_openhost_databases(self, which, mysql_query):
        which.side_effect = lambda name: '/usr/bin/mariadb' if name == 'mariadb' else None
        result = agent.list_databases()
        self.assertTrue(result['ok'])
        self.assertEqual([x['name'] for x in result['databases']], ['oh_0123456789'])


    @patch.object(agent, 'mysql_query')
    @patch.object(agent.shutil, 'which')
    def test_delete_requires_exact_confirmation(self, which, mysql_query):
        which.return_value = '/usr/bin/mariadb'
        invalid = agent.delete_database('externaldb', 'externaldb')
        self.assertFalse(invalid['ok'])
        mysql_query.assert_not_called()
        mismatch = agent.delete_database('oh_0123456789', 'wrong')
        self.assertFalse(mismatch['ok'])
        mysql_query.assert_not_called()

    @patch.object(agent.shutil, 'which', return_value=None)
    def test_backup_requires_dump_utility(self, which):
        result = agent.backup_database('oh_0123456789')
        self.assertFalse(result['ok'])
        self.assertIn('belum terpasang', result['error'])

if __name__ == '__main__':
    unittest.main()
