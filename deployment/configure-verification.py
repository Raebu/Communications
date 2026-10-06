"""Store verification credentials privately; leave live activation disabled."""
from getpass import getpass
from pathlib import Path
import os
import re

path = Path('.env')
if not path.is_file():
    raise SystemExit('Run in the production repository containing .env.')
registry = getpass('Companies House read-only API key (hidden): ').strip()
identity = getpass('Stripe Identity restricted LIVE API key (hidden): ').strip()
if not registry or not re.fullmatch(r'[A-Za-z0-9_-]+', registry):
    raise SystemExit('Invalid registry key format; no settings changed.')
if not re.fullmatch(r'rk_live_[A-Za-z0-9]+', identity):
    raise SystemExit('Use a restricted live Stripe Identity key; no settings changed.')
updates = {'COMPANIES_HOUSE_API_KEY':registry,'STRIPE_IDENTITY_KEY':identity,
           'COMPANY_VERIFICATION_ENABLED':'false','VERIFICATION_AUTO_TWILIO':'false','VERIFICATION_DAILY_LIMIT':'20'}
lines = [line for line in path.read_text().splitlines() if line.split('=',1)[0] not in updates]
lines.extend(name+'='+value for name,value in updates.items())
temporary = path.with_name('.env.verification-tmp')
fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
try:
    with os.fdopen(fd,'w') as output:
        output.write('\n'.join(lines)+'\n')
    os.replace(temporary,path)
finally:
    if temporary.exists():
        temporary.unlink()
print('Verification keys stored privately. Live verification remains disabled until acceptance configuration is complete.')
