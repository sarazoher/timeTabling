import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from auth_support import AuthFixture, app
from auth import database, digest, init_database
from password_reset import issue_token, local_reset, redeem_token, token_valid, send_reset_email, email_ready

class PasswordResetTests(AuthFixture, unittest.TestCase):
    def login(self, password):
        return self.client.post('/login', data={'csrf_token': self.csrf(), 'email': 'marah@example.com', 'password': password})

    def test_no_email_config_gives_local_instructions_without_reset(self):
        self.register(); self.logout()
        with patch('password_reset.email_ready', return_value=False):
            r = self.client.get('/forgot-password')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'reset_password.py', r.data)
        self.assertEqual(self.login('A long test password!').status_code, 302)

    def test_reset_request_same_response_for_known_and_unknown_email(self):
        self.register(); self.logout()
        with patch('password_reset.email_ready', return_value=True), patch('password_reset.send_reset_email') as send:
            responses = [self.client.post('/forgot-password', data={'csrf_token': self.csrf(), 'email': e})
                         for e in ['marah@example.com', 'unknown@example.com']]
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(responses[0].data, responses[1].data)
        send.assert_called_once()
        token = send.call_args.args[1]
        self.assertNotIn(token.encode(), responses[0].data)
        with database(app.app) as db:
            row = db.execute('SELECT * FROM password_resets').fetchone()
        self.assertEqual(row['token_hash'], digest(token))
        self.assertNotEqual(row['token_hash'], token)

    def test_reset_page_changes_password_and_revokes_old_sessions(self):
        self.register()
        cookie = self.client.get_cookie('session').value
        token = issue_token(app.app, 'marah@example.com')
        self.assertEqual(self.client.get('/reset-password?token='+token).status_code, 200)
        r = self.client.post('/reset-password', data={'csrf_token': self.csrf(), 'token': token,
                           'password': 'New password 123!', 'confirm_password': 'New password 123!'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('password_reset=1', r.location)
        copied = app.app.test_client(); copied.set_cookie('session', cookie)
        self.assertEqual(copied.get('/').status_code, 302)
        self.assertEqual(self.login('A long test password!').status_code, 401)
        self.assertEqual(self.login('New password 123!').status_code, 302)
        self.assertFalse(redeem_token(app.app, token, 'Another password!'))

    def test_expired_and_invalid_links_rejected(self):
        self.register(); self.logout()
        token = issue_token(app.app, 'marah@example.com')
        with database(app.app) as db:db.execute('UPDATE password_resets SET expires_at=0')
        for value in [token, 'invalid']:
            self.assertEqual(self.client.get('/reset-password?token='+value).status_code, 400)
            self.assertFalse(redeem_token(app.app, value, 'New password 123!'))

    def test_invalid_password_and_csrf_do_not_consume_token(self):
        self.register(); self.logout(); token = issue_token(app.app, 'marah@example.com')
        self.assertEqual(self.client.post('/reset-password', data={'token':token}).status_code, 400)
        self.assertEqual(self.client.post('/forgot-password').status_code, 400)
        for password, confirmation in [('short','short'), ('Long password 123!', 'does not match')]:
            r = self.client.post('/reset-password', data={'csrf_token':self.csrf(), 'token':token,
                         'password':password, 'confirm_password':confirmation})
            self.assertEqual(r.status_code, 400)
            self.assertTrue(token_valid(app.app, token))

    def test_new_link_revokes_previous_link(self):
        self.register(); a = issue_token(app.app, 'marah@example.com'); b = issue_token(app.app, 'marah@example.com')
        self.assertFalse(token_valid(app.app,a));self.assertTrue(token_valid(app.app,b))

    def test_local_reset_preserves_users_and_revokes_tokens_sessions(self):
        self.register(); token = issue_token(app.app, 'marah@example.com')
        self.assertTrue(local_reset(app.app,'MARAH@example.com','Replacement password!'))
        self.assertFalse(token_valid(app.app,token))
        self.assertEqual(self.client.get('/calendar').status_code,302)
        self.assertEqual(self.login('Replacement password!').status_code,302)
        with database(app.app) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
        self.assertFalse(local_reset(app.app,'unknown@example.com','Replacement password!'))

    def test_delivery_failure_does_not_expose_link(self):
        self.register();self.logout()
        with patch('password_reset.email_ready',return_value=True), patch('password_reset.send_reset_email',side_effect=RuntimeError('SMTP secret')):
            with self.assertLogs(app.app.logger,level='WARNING') as logs:
                response=self.client.post('/forgot-password',data={'csrf_token':self.csrf(),'email':'marah@example.com'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('SMTP secret',' '.join(logs.output))
        with database(app.app) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM password_resets').fetchone()[0],0)

    def test_reset_requests_are_rate_limited(self):
        with patch('password_reset.email_ready',return_value=True):
            for _ in range(10):
                self.assertEqual(self.client.post('/forgot-password',data={'csrf_token':self.csrf(),'email':'unknown@example.com'}).status_code,200)
            self.assertEqual(self.client.post('/forgot-password',data={'csrf_token':self.csrf(),'email':'unknown@example.com'}).status_code,429)

    def test_email_uses_configured_url_and_tls(self):
        config = {'TIMETABLE_SMTP_HOST':'mail.example', 'TIMETABLE_SMTP_FROM':'no-reply@example.com',
                  'TIMETABLE_PUBLIC_URL':'https://trusted.example', 'TIMETABLE_SMTP_SECURITY':'starttls',
                  'TIMETABLE_SMTP_USER':'operator', 'TIMETABLE_SMTP_PASSWORD':'test-only-secret'}
        with patch.dict('os.environ', config, clear=True), patch('password_reset.smtplib.SMTP') as transport:
            with app.app.test_request_context('/', headers={'Host':'attacker.example'}):
                self.assertTrue(email_ready())
                send_reset_email('marah@example.com', 'test-token')
        smtp = transport.return_value.__enter__.return_value
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with('operator', 'test-only-secret')
        body = smtp.send_message.call_args.args[0].get_content()
        self.assertIn('https://trusted.example/reset-password?token=test-token', body)
        self.assertNotIn('attacker.example', body)

    def test_reset_affects_only_token_owner(self):
        self.register(); self.logout(); self.register('second@example.com'); self.logout()
        token = issue_token(app.app, 'marah@example.com')
        self.assertTrue(redeem_token(app.app, token, 'Updated password!'))
        response = self.client.post('/login', data={'csrf_token':self.csrf(),
                      'email':'second@example.com','password':'A long test password!'})
        self.assertEqual(response.status_code,302)

    def test_local_command_and_existing_database_migration(self):
        self.register();init_database(app.app)
        spec=importlib.util.spec_from_file_location('reset_cli',Path(__file__).resolve().parents[1]/'reset_password.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch('builtins.input',return_value='marah@example.com'),patch('getpass.getpass',side_effect=['Terminal password!', 'Terminal password!']),patch('builtins.print'):
            self.assertEqual(module.main(),0)
        self.logout()
        self.assertEqual(self.login('Terminal password!').status_code,302)

if __name__=='__main__':unittest.main()
