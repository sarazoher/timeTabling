import unittest
from unittest.mock import patch
from werkzeug.security import check_password_hash
from auth_support import AuthFixture, app
from auth import database, local_next

class AccountTests(AuthFixture, unittest.TestCase):
    def test_registration_hashes_password_and_signs_in(self):
        self.assertEqual(self.register().status_code, 302)
        with database(app.app) as db:
            row = db.execute('SELECT * FROM users').fetchone()
        self.assertNotIn('A long test password!', row['password_hash'])
        self.assertTrue(check_password_hash(row['password_hash'], 'A long test password!'))
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_all_workspace_routes_require_login(self):
        for rule in app.app.url_map.iter_rules():
            if rule.endpoint in {'login', 'register', 'set_language', 'static', 'forgot_password', 'reset_password'}:
                continue
            path = str(rule).replace('<rule_id>', 'example')
            with self.subTest(route=path):
                response = self.client.open(path, method='GET' if 'GET' in rule.methods else 'POST')
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.location.startswith('/login?'))

    def test_correct_login_and_case_insensitive_email(self):
        self.register(); self.logout()
        response = self.client.post('/login', data={'csrf_token': self.csrf(),
            'email': 'MARAH@EXAMPLE.COM', 'password': 'A long test password!', 'next': '/calendar'})
        self.assertEqual(response.location, '/calendar')

    def test_wrong_password_and_unknown_email(self):
        self.register(); self.logout()
        for email in ['marah@example.com', 'missing@example.com']:
            response = self.client.post('/login', data={'csrf_token': self.csrf(), 'email': email, 'password': 'bad'})
            self.assertEqual(response.status_code, 401)
            self.assertIn('כתובת האימייל או הסיסמה שגויות.'.encode(), response.data)
        self.assertEqual(self.client.get('/calendar').status_code, 302)

    def test_duplicate_email_rejected(self):
        self.register(); self.logout()
        self.assertEqual(self.register('MARAH@example.com').status_code, 409)
        with database(app.app) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)

    def test_short_password_and_mismatch_rejected(self):
        self.assertEqual(self.register(password='short').status_code, 400)
        response = self.client.post('/register', data={'csrf_token': self.csrf(), 'name': 'Name',
            'email': 'test@example.com', 'password': 'long enough password', 'confirm_password': 'different'})
        self.assertEqual(response.status_code, 400)

    def test_csrf_required_for_auth_and_workspace(self):
        self.assertEqual(self.client.post('/register', data={}).status_code, 400)
        self.assertEqual(self.client.post('/login', data={}).status_code, 400)
        self.register()
        with patch.object(app, 'run_backend', side_effect=AssertionError('must not run')):
            self.assertEqual(self.client.post('/generate').status_code, 400)
            self.assertEqual(self.client.post('/generate', data={'csrf_token': 'עברית'}).status_code, 400)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 400)

    def test_logout_revokes_copied_session(self):
        self.register()
        old_cookie = self.client.get_cookie('session').value
        self.assertEqual(self.logout().status_code, 302)
        copied = app.app.test_client(); copied.set_cookie('session', old_cookie)
        self.assertEqual(copied.get('/calendar').status_code, 302)

    def test_expired_session_rejected(self):
        self.register()
        with database(app.app) as db:
            db.execute('UPDATE auth_sessions SET expires_at=0')
        self.assertEqual(self.client.get('/export/timetable').status_code, 302)

    def test_login_prevents_external_redirect(self):
        for target in ['//evil.example', 'https://evil.example', '/\\evil', 'http://[broken']:
            self.assertEqual(local_next(target), '/')
        self.register(); self.logout()
        response = self.client.post('/login', data={'csrf_token': self.csrf(), 'email': 'marah@example.com',
            'password': 'A long test password!', 'next': '//evil.example'})
        self.assertEqual(response.location, '/')

    def test_login_rate_limit(self):
        self.register(); self.logout()
        for _ in range(5):
            self.assertEqual(self.client.post('/login', data={'csrf_token': self.csrf(),
                'email': 'marah@example.com', 'password': 'bad'}).status_code, 401)
        self.assertEqual(self.client.post('/login', data={'csrf_token': self.csrf(),
            'email': 'marah@example.com', 'password': 'bad'}).status_code, 429)

    def test_languages_and_authenticated_pages_render(self):
        self.client.get('/language/en')
        self.assertIn(b'Sign in to your account', self.client.get('/login').data)
        self.register()
        for path in ['/', '/calendar', '/days', '/lecturers']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Sign out', response.data)
            self.assertIn(b'name="csrf_token"', response.data)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_authenticated_form_still_works(self):
        self.register()
        with self.client.session_transaction() as sess:
            token = sess['csrf_token']
        with patch.object(app, 'save_config') as save:
            response = self.client.post('/days/save', data={'csrf_token': token,
                'degree_years': '3', 'semesters_per_year': '3',
                'enabled_Monday': 'on', 'start_Monday': '08:00', 'end_Monday': '18:00'})
        self.assertEqual(response.status_code, 302)
        save.assert_called_once()

    def test_accounts_survive_new_client_and_database_reopen(self):
        self.register(); self.logout()
        other = app.app.test_client()
        response = other.post('/login', data={'csrf_token': self.csrf(other),
            'email': 'marah@example.com', 'password': 'A long test password!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(other.get('/').status_code, 200)

if __name__ == '__main__':unittest.main()
