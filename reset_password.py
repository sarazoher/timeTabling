r"""Run locally from PowerShell: .\.venv\Scripts\python.exe reset_password.py"""
import getpass
from pathlib import Path
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parent / 'timetable_frontend'))


def main():
    from app import app
    from auth import database
    from password_reset import local_reset, valid_password
    print('Local Timetable account recovery. Existing courses and timetables are preserved.')
    email = input('Registered email address: ').strip().lower()
    with database(app) as db:
        exists = db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone()
    if not exists:
        print('Account not found. Check the email and run this from your existing project with its .instance folder.')
        return 1
    # Refuse echoing passwords in an unsupported terminal.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('New password (10–256 characters; typing is hidden): ')
        confirmation = getpass.getpass('Confirm new password: ')
    if password != confirmation:
        print('Passwords do not match. No changes made.')
        return 1
    if not valid_password(password):
        print('Password must contain 10–256 characters. No changes made.')
        return 1
    if not local_reset(app, email, password):
        print('Account not found. No changes made.')
        return 1
    print('Password reset. Sign in again. All previous sessions and reset links were revoked.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print('\nReset cancelled. Use an interactive PowerShell terminal.')
        raise SystemExit(1)
