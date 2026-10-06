"""Run from the repository root on the production server; never prints credentials."""
from getpass import getpass
from pathlib import Path
import os
import re

path = Path('.env')
if not path.is_file():
    raise SystemExit('Run from the repository root containing your existing .env.')
key = getpass('Paste the Resend sending API key (hidden): ').strip()
if not re.fullmatch(r're_[A-Za-z0-9_-]+', key):
    raise SystemExit('Invalid Resend key format; no settings changed.')
updates = {
    'SMTP_HOST': 'smtp.resend.com', 'SMTP_PORT': '465', 'SMTP_USER': 'resend',
    'SMTP_PASSWORD': key,
    'EMAIL_FROM': 'newphoneline@theraeburngroup.com',
    'EMAIL_REPLY_TO': 'support@theraeburngroup.com',
    'SUPPORT_EMAIL': 'support@theraeburngroup.com',
    'VERIFICATION_EMAIL_FROM': 'verification@theraeburngroup.com',
    'COMPANY_REVIEW_EMAIL': 'verification@theraeburngroup.com',
    'BILLING_EMAIL': 'billing@theraeburngroup.com',
}
lines = []
for line in path.read_text().splitlines():
    name = line.split('=', 1)[0]
    if name not in updates:
        lines.append(line)
lines.extend(name + '=' + value for name, value in updates.items())
# Atomic replacement with private permissions, including during the write.
temporary = path.with_name('.env.email-tmp')
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, 'w') as output:
        output.write('\n'.join(lines) + '\n')
    os.replace(temporary, path)
finally:
    if temporary.exists():
        temporary.unlink()
print('Email settings saved privately. Recreate the API and worker to apply them.')
