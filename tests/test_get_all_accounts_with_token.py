import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import utils.db_manager as db_manager


class GetAllAccountsWithTokenTests(unittest.TestCase):
    def test_limit_zero_returns_all_accounts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / 'data.db'
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute('CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, password TEXT, token_data TEXT)')
            cur.executemany(
                'INSERT INTO accounts (email, password, token_data) VALUES (?, ?, ?)',
                [
                    ('one@example.com', 'pw1', '{"access_token":"one"}'),
                    ('two@example.com', 'pw2', '{"access_token":"two"}'),
                ],
            )
            conn.commit()
            conn.close()

            with patch.object(db_manager, 'DB_PATH', str(db_path)):
                rows = db_manager.get_all_accounts_with_token(0, 0)

        self.assertEqual(2, len(rows))
        self.assertEqual('two@example.com', rows[0]['email'])
        self.assertEqual('one@example.com', rows[1]['email'])


if __name__ == '__main__':
    unittest.main()
