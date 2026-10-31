"""SQLite accounts and revocable sessions for the shared department workspace."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from flask import g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def text(he, en):
    return en if session.get('lang', 'he') == 'en' else he


@contextmanager
def database(app):
    connection = sqlite3.connect(app.config['AUTH_DATABASE'], timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_database(app):
    Path(app.config['AUTH_DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    with database(app) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                ip TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS attempts_time ON auth_attempts(created_at);
        ''')


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def local_next(value):
    # Never redirect login to an external, scheme-relative or backslash URL.
    value = value or ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "/"
    if (value.startswith('/') and not value.startswith('//') and '\\' not in value
            and not parsed.scheme and not parsed.netloc
            and not any(ord(c) < 32 for c in value)):
        return value
    return '/'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def install_auth(app):
    state = Path(os.environ.get('TIMETABLE_AUTH_DIR', str(Path(app.root_path).parent / '.instance')))
    state.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get('TIMETABLE_SECRET_KEY')
    if not secret:
        key_file = state / 'secret.key'
        try:
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            secret = key_file.read_text().strip()
        else:
            secret = secrets.token_hex(32)
            with os.fdopen(fd, 'w') as output:
                output.write(secret)
    if not secret or len(secret) < 32:
        raise RuntimeError('TIMETABLE_SECRET_KEY must contain at least 32 characters.')
    app.secret_key = secret
    app.config.update(
        AUTH_DATABASE=str(state / 'accounts.sqlite3'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('TIMETABLE_HTTPS') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    init_database(app)
    # Unknown-email login uses the same password-hash path as known accounts.
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')

    def end_session():
        token = session.get('auth_token')
        if token:
            with database(app) as db:
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(token),))
        lang = session.get('lang', 'he')
        session.clear()
        session['lang'] = lang

    def begin_session(user_id):
        end_session()
        token = secrets.token_urlsafe(32)
        with database(app) as db:
            db.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (int(time.time()),))
            db.execute('INSERT INTO auth_sessions VALUES (?, ?, ?)',
                       (digest(token), user_id, int(time.time()) + 8 * 3600))
        session['auth_token'] = token
        session.permanent = True
        csrf_token()

    def attempt_allowed(kind, email):
        now = int(time.time())
        ip = request.remote_addr or 'unknown'
        window, ip_limit, email_limit = (900, 30, 5) if kind == 'login' else (3600, 10, 10)
        # Serialize count + insert so parallel requests cannot bypass the cap.
        with database(app) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_attempts WHERE created_at<?', (now - 3600,))
            ip_count = db.execute('SELECT COUNT(*) FROM auth_attempts WHERE kind=? AND ip=? AND created_at>?',
                                  (kind, ip, now - window)).fetchone()[0]
            email_count = db.execute('SELECT COUNT(*) FROM auth_attempts WHERE kind=? AND email=? AND created_at>?',
                                     (kind, email, now - window)).fetchone()[0]
            if ip_count >= ip_limit or email_count >= email_limit:
                return False
            db.execute('INSERT INTO auth_attempts(kind,ip,email,created_at) VALUES(?,?,?,?)',
                       (kind, ip, email, now))
        return True

    @app.context_processor
    def auth_context():
        return dict(current_user=g.get('user'), csrf_token=csrf_token, auth_text=text)

    @app.before_request
    def protect_workspace():
        g.user = None
        token = session.get('auth_token')
        if isinstance(token, str):
            with database(app) as db:
                row = db.execute('''SELECT users.id, users.name, users.email FROM users
                    JOIN auth_sessions ON users.id=auth_sessions.user_id
                    WHERE token_hash=? AND expires_at>?''', (digest(token), int(time.time()))).fetchone()
            if row:
                g.user = dict(row)
            else:
                session.pop('auth_token', None)
        public = {'login', 'register', 'set_language', 'static', 'forgot_password', 'reset_password'}
        if request.endpoint and request.endpoint not in public and g.user is None:
            return redirect(url_for('login', next=request.full_path.rstrip('?')))
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            expected = session.get('csrf_token', '')
            supplied = request.form.get('csrf_token', '') or request.headers.get('X-CSRF-Token', '')
            if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
                return render_template('auth_error.html', message=text(
                    'הטופס פג תוקף. חזרו לעמוד, רעננו ונסו שוב.',
                    'This form has expired. Return to the page, refresh and try again.')), 400

    @app.after_request
    def private_responses(response):
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store'
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        return response

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user:
            return redirect(url_for('setup_page'))
        error = None
        code = 200
        email = request.form.get('email', '').strip().lower()
        if request.method == 'POST':
            if not attempt_allowed('login', email[:254]):
                error, code = text('יותר מדי ניסיונות. נסו שוב בעוד 15 דקות.',
                                   'Too many attempts. Try again in 15 minutes.'), 429
            else:
                with database(app) as db:
                    user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                password = request.form.get('password', '')
                valid = check_password_hash(user['password_hash'] if user else dummy_hash, password[:256])
                if user and valid and len(password) <= 256:
                    with database(app) as db:
                        db.execute("DELETE FROM auth_attempts WHERE kind='login' AND email=?", (email,))
                    begin_session(user['id'])
                    return redirect(local_next(request.form.get('next')))
                error, code = text('כתובת האימייל או הסיסמה שגויות.', 'Incorrect email or password.'), 401
        return render_template('auth.html', registering=False, error=error, email=email,
                               next_url=local_next(request.values.get('next'))), code

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if g.user:
            return redirect(url_for('setup_page'))
        error = None
        code = 200
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', '').strip()
        if request.method == 'POST':
            password = request.form.get('password', '')
            if not attempt_allowed('register', email[:254]):
                error, code = text('יותר מדי ניסיונות הרשמה. נסו שוב מאוחר יותר.',
                                   'Too many registration attempts. Try again later.'), 429
            elif not 1 <= len(name) <= 80 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
                error, code = text('יש להזין שם וכתובת אימייל תקינים.', 'Enter a valid name and email address.'), 400
            elif not 10 <= len(password) <= 256:
                error, code = text('הסיסמה חייבת להכיל 10–256 תווים.', 'Use a password with 10–256 characters.'), 400
            elif password != request.form.get('confirm_password'):
                error, code = text('הסיסמאות אינן תואמות.', 'Passwords do not match.'), 400
            else:
                try:
                    with database(app) as db:
                        cursor = db.execute('INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)',
                            (name, email, generate_password_hash(password, method='scrypt'), int(time.time())))
                        user_id = cursor.lastrowid
                except sqlite3.IntegrityError:
                    error, code = text('לא ניתן ליצור חשבון עם כתובת זו. נסו להתחבר.',
                                       'Cannot create an account with this address. Try signing in.'), 409
                else:
                    begin_session(user_id)
                    # A new account starts empty: go straight to the guided setup.
                    return redirect(url_for('guided_setup'))
        return render_template('auth.html', registering=True, error=error, email=email,
                               name=name, next_url='/'), code

    @app.post('/logout')
    def logout():
        end_session()
        return redirect(url_for('login'))

    from password_reset import install_password_reset
    install_password_reset(app, attempt_allowed, end_session)
