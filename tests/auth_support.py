import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'timetable_frontend'))
import app
from auth import init_database

class AuthFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Accounts and their workspaces live in a temporary folder, so tests
        # never touch real data.
        self.config_patch = patch.dict(app.app.config, {
            'TESTING': True, 'AUTH_DATABASE': str(Path(self.temp.name) / 'accounts.sqlite3'),
            'WORKSPACES_DIR': str(Path(self.temp.name) / 'workspaces')})
        self.config_patch.start()
        init_database(app.app)
        self.client = app.app.test_client()

    def tearDown(self):
        self.config_patch.stop()
        self.temp.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        client.get('/login')
        with client.session_transaction() as sess:
            return sess['csrf_token']

    def register(self, email='marah@example.com', password='A long test password!', client=None):
        client = client or self.client
        return client.post('/register', data={
            'csrf_token': self.csrf(client), 'name': 'مرح', 'email': email,
            'password': password, 'confirm_password': password})

    def logout(self):
        with self.client.session_transaction() as sess:
            token = sess['csrf_token']
        return self.client.post('/logout', data={'csrf_token': token})

    def workspace(self, email='marah@example.com'):
        """The workspace folder of a registered account (created if needed)."""
        from auth import database
        with database(app.app) as db:
            user_id = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()[0]
        folder = Path(app.app.config['WORKSPACES_DIR']) / f'user_{user_id}'
        folder.mkdir(parents=True, exist_ok=True)
        return folder
