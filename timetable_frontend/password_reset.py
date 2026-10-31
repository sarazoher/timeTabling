"""Password recovery: short-lived email tokens and a local operator reset."""
from email.message import EmailMessage
import os
import secrets
import smtplib
import ssl
import time
from urllib.parse import urlsplit

from flask import redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash
from auth import database, digest, text


def valid_password(password):
    return 10 <= len(password) <= 256


def email_settings():
    return {key: os.environ.get('TIMETABLE_' + key, '') for key in
            ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM', 'PUBLIC_URL', 'SMTP_SECURITY']}


def email_ready():
    cfg = email_settings()
    try:
        parsed = urlsplit(cfg['PUBLIC_URL'])
        # Build links from configured deployment URL, never an HTTP Host header.
        valid_url = (parsed.scheme == 'https' or
                     (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1'}))
        return bool(cfg['SMTP_HOST'] and cfg['SMTP_FROM'] and parsed.netloc and valid_url
                    and not parsed.query and not parsed.fragment and not parsed.username)
    except ValueError:
        return False


def send_reset_email(recipient, token):
    cfg = email_settings()
    url = cfg['PUBLIC_URL'].rstrip('/') + '/reset-password?token=' + token
    message = EmailMessage()
    message['Subject'] = 'Timetable — password reset / איפוס סיסמה'
    message['From'] = cfg['SMTP_FROM']
    message['To'] = recipient
    message.set_content('To set a new password, open this link within 30 minutes:\n'
                        'לאיפוס הסיסמה, פתחו את הקישור בתוך 30 דקות:\n\n' + url + '\n\n'
                        'If you did not request this, ignore this email. Your password has not changed.\n'
                        'אם לא ביקשתם איפוס, אפשר להתעלם מההודעה. הסיסמה לא השתנתה.')
    security = cfg['SMTP_SECURITY'] or 'starttls'
    if security not in {'starttls', 'ssl'}:
        raise ValueError('SMTP_SECURITY must be starttls or ssl.')
    port = int(cfg['SMTP_PORT'] or ('465' if security == 'ssl' else '587'))
    transport = smtplib.SMTP_SSL if security == 'ssl' else smtplib.SMTP
    options = {'timeout': 10}
    if security == 'ssl':
        options['context'] = ssl.create_default_context()
    with transport(cfg['SMTP_HOST'], port, **options) as smtp:
        if security == 'starttls':
            smtp.starttls(context=ssl.create_default_context())
        if cfg['SMTP_USER']:
            smtp.login(cfg['SMTP_USER'], cfg['SMTP_PASSWORD'])
        smtp.send_message(message)


def issue_token(app, email):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with database(app) as db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if user is None:
            return None
        db.execute('DELETE FROM password_resets WHERE expires_at<=? OR user_id=?', (now, user['id']))
        db.execute('INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)',
                   (digest(token), user['id'], now + 1800))
    return token


def token_valid(app, token):
    if not token or len(token) > 512:
        return False
    with database(app) as db:
        return db.execute('SELECT 1 FROM password_resets WHERE token_hash=? AND expires_at>?',
                          (digest(token), int(time.time()))).fetchone() is not None


def update_password(db, user_id, email, password_hash):
    db.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash, user_id))
    db.execute('DELETE FROM auth_sessions WHERE user_id=?', (user_id,))
    db.execute('DELETE FROM password_resets WHERE user_id=?', (user_id,))
    db.execute("DELETE FROM auth_attempts WHERE email=? AND kind IN ('login','reset')", (email,))


def redeem_token(app, token, password):
    if not valid_password(password):
        raise ValueError('Use 10–256 characters.')
    if not token or len(token) > 512:
        return False
    password_hash = generate_password_hash(password, method='scrypt')
    with database(app) as db:
        # Consumption and password/session updates are one transaction.
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''SELECT users.id, users.email FROM password_resets
                            JOIN users ON users.id=password_resets.user_id
                            WHERE token_hash=? AND expires_at>?''', (digest(token), int(time.time()))).fetchone()
        if row is None:
            return False
        update_password(db, row['id'], row['email'], password_hash)
    return True


def local_reset(app, email, password):
    """Only for an operator with local filesystem/database write access."""
    if not valid_password(password):
        raise ValueError('Use 10–256 characters.')
    password_hash = generate_password_hash(password, method='scrypt')
    with database(app) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT id,email FROM users WHERE email=?', (email.strip().lower(),)).fetchone()
        if row is None:
            return False
        update_password(db, row['id'], row['email'], password_hash)
    return True


def install_password_reset(app, attempt_allowed, end_session):
    @app.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        ready = email_ready()
        message, error, status = None, None, 200
        if request.method == 'POST' and ready:
            email = request.form.get('email', '').strip().lower()[:254]
            if not attempt_allowed('reset', email):
                error, status = text('יותר מדי ניסיונות. נסו שוב מאוחר יותר.',
                                     'Too many requests. Please try again later.'), 429
            else:
                token = issue_token(app, email)
                if token:
                    try:
                        send_reset_email(email, token)
                    except Exception:
                        # Do not disclose email existence, credentials or reset links in logs.
                        with database(app) as db:
                            db.execute('DELETE FROM password_resets WHERE token_hash=?', (digest(token),))
                        app.logger.warning('Password-reset delivery failed; check SMTP configuration.')
                message = text('אם קיים חשבון בכתובת הזו, נשלח אליו קישור לאיפוס הסיסמה. בדקו גם בתיקיית הספאם.',
                               'If an account exists for this address, a password-reset link has been sent. Check your spam folder too.')
        return render_template('password_reset.html', mode='request', ready=ready,
                               message=message, error=error), status

    @app.route('/reset-password', methods=['GET', 'POST'])
    def reset_password():
        token = request.form.get('token', '') if request.method == 'POST' else request.args.get('token', '')
        if not token_valid(app, token):
            return render_template('password_reset.html', mode='invalid'), 400
        error = None
        if request.method == 'POST':
            password = request.form.get('password', '')
            if not valid_password(password):
                error = text('הסיסמה חייבת להכיל 10–256 תווים.', 'Use a password with 10–256 characters.')
            elif password != request.form.get('confirm_password'):
                error = text('הסיסמאות אינן תואמות.', 'Passwords do not match.')
            elif redeem_token(app, token, password):
                end_session()
                return redirect(url_for('login', password_reset='1'))
            else:
                return render_template('password_reset.html', mode='invalid'), 400
        return render_template('password_reset.html', mode='reset', token=token, error=error), (400 if error else 200)
