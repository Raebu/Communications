"""Fail-closed UK company authority and domain verification.

A mailbox/DNS proof never establishes company authority on its own. Personal
identity is retrieved from Stripe and matched to a current registered director.
No applicant-controlled URL is fetched and no identity document is stored here.
"""
import hashlib
from io import BytesIO
import hmac
import json
import re
import secrets
import unicodedata
from collections import Counter
from datetime import timedelta
from urllib.parse import urlsplit

import dns.resolver
import httpx
import qrcode
import stripe
import tldextract
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from qrcode.image.svg import SvgPathImage
from sqlalchemy.exc import IntegrityError

from .config import settings
from .models import ActionToken, Audit, CompanyVerification, DB, EmailJob, RateBucket, Tenant, User, VerifiedCompanyClaim, now, uid
from .security import csrf, current_user, decrypt, encrypt, rate_limit

router = APIRouter(prefix='/api/company-verification')
# Bundled PSL only: no startup network access or user-selected suffix source.
extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)
FREE_MAIL = {'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com',
             'yahoo.com', 'yahoo.co.uk', 'icloud.com', 'aol.com', 'proton.me', 'protonmail.com'}
MESSAGES = {
    'pending': 'Thank you — we are checking your company and domain. Complete the steps below while we work.',
    'verified': 'Your company, domain and director authority checks are complete. Telephone approval is handled separately.',
    'held': 'Your activation is safely on hold. Please check the explanation below before continuing.',
    'expired': 'This verification has expired. Start a new check to receive fresh proof instructions.',
    'invalidated': 'Your company details changed. Start verification again so the checks match your current details.',
}
REASONS = {
    'domain_risk': 'This domain resembles a protected business domain. Automatic activation is unavailable for this claim.',
    'claim_conflict': 'This company or domain already has a verified account. Use that account or contact support.',
    'registry_mismatch': 'Use the exact legal name and complete registered office address shown at Companies House.',
    'registry_ineligible': 'The register does not show an eligible active UK limited company for automatic verification.',
    'authority_mismatch': 'The verified identity must match a current individual director, including birth month and year. An employee email alone cannot establish authority.',
    'provider_unavailable': 'A verification service is temporarily unavailable. We will retry automatically.',
    'provider_requirements': 'The telephone provider needs information outside the supported automated requirements. Activation remains on hold.',
    'provider_rejected': 'The telephone provider has not approved this application. Activation remains on hold.',
    'provider_uncertain': 'The director identity provider returned an uncertain result. Your company checks remain verified and the identity step can be retried safely.',
    'identity_configuration': 'The director identity service rejected the request before verification could start. Check the Stripe Identity API key permissions/configuration, then retry this step.',
}


def aware(value):
    return value.replace(tzinfo=now().tzinfo)


def profile_hash(tenant):
    return hashlib.sha256(json.dumps([tenant.legal_name, tenant.address, tenant.registration_number],
                                     ensure_ascii=False).encode()).hexdigest()


def canonical_domain(raw):
    # Reject IDN/punycode, invisible characters, ports, paths, userinfo and
    # subdomain tricks rather than guessing that a similar string is trusted.
    if not raw.isascii() or raw != raw.strip() or len(raw) > 253:
        raise ValueError('Use a plain ASCII registered business domain; internationalised domains need additional verification.')
    value = raw.lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
        raise ValueError('Enter only your domain, such as example.co.uk, without a URL or path.')
    labels = value.split('.')
    if any(not label or len(label) > 63 or label.startswith(('xn--', '-')) or label.endswith('-') for label in labels):
        raise ValueError('This domain cannot be automatically verified.')
    parts = extract(value)
    if not parts.suffix or not parts.domain or parts.subdomain or parts.is_private:
        raise ValueError('Use your registered domain, not a subdomain, public suffix or shared hosting domain.')
    if value in FREE_MAIL:
        raise ValueError('Use your company domain, not a personal email provider.')
    return value


def skeleton(domain):
    label = extract(domain).domain.lower().replace('-', '')
    return label.replace('rn', 'm').replace('vv', 'w').translate(str.maketrans('0158', 'olsb')).replace('i', 'l')


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        row = [i]
        for j, right in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = row
    return previous[-1]


def risky_domain(domain, protected):
    a = skeleton(domain)
    for other in protected:
        b = skeleton(other)
        if domain == other or a == b:
            return True
        if min(len(a), len(b)) >= 5 and edit_distance(a, b) <= 2:
            return True
        if len(b) >= 5 and (a.startswith(b) or a.endswith(b)):
            return True
    return False


def protected_for(db, tenant_id):
    configured = [x.strip().lower() for x in settings.protected_domains.split(',') if x.strip()]
    claimed = db.scalars(select(VerifiedCompanyClaim.domain).where(VerifiedCompanyClaim.tenant_id != tenant_id)).all()
    return set(configured + list(claimed) + list(json.loads(settings.trusted_company_domains).values()))


def claim_domain_risky(db, number, domain, tenant_id):
    trusted = json.loads(settings.trusted_company_domains)
    anchor = trusted.get(number)
    if anchor and anchor != domain:
        return True
    protected = protected_for(db, tenant_id)
    if anchor == domain:
        protected.discard(domain)
    return risky_domain(domain, protected)


def require_company_verified(db, tenant):
    if not settings.company_verification_enabled and settings.environment != 'production':
        return
    proof = db.get(CompanyVerification, tenant.id)
    if not proof or proof.status != 'verified' or proof.reason or not all([proof.email_verified, proof.dns_verified, proof.registry_verified, proof.authority_verified]) or proof.profile_hash != profile_hash(tenant):
        raise HTTPException(409, 'Complete company, domain and authority verification before activation')
    if proof.next_check_at and aware(proof.next_check_at) < now() - timedelta(hours=1):
        raise HTTPException(409, 'Company verification is awaiting its scheduled refresh')


VERIFICATION_EMAIL_EVENTS = {
    'business_email',
    'verification_started',
    'company_verified',
    'director_verified',
}


def verification_event_mail(db, proof, event, recipient, subject, body):
    if event not in VERIFICATION_EMAIL_EVENTS:
        raise ValueError('unsupported verification email event')
    key = 'verification-email:' + proof.attempt + ':' + event
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    result = db.execute(
        insert(RateBucket)
        .values(key=key, count=1, expires_at=proof.expires_at + timedelta(days=2))
        .on_conflict_do_nothing(index_elements=['key'])
        .returning(RateBucket.key)
    ).scalar_one_or_none()
    if not result:
        return False
    db.add(EmailJob(recipient=recipient, encrypted_payload=encrypt({
        'from': settings.verification_email_from or settings.email_from,
        'subject': subject + ' — Raeburn Connect',
        'body': body + '\n\n' + settings.public_url + '/',
        'category': 'company_verification',
        'event': event,
        'attempt': proof.attempt,
    })))
    return True


def email_challenge(db, proof):
    if proof.email_sends:
        raise HTTPException(409, 'The business email verification message has already been sent for this verification attempt')
    proof.email_sends = 1
    token = secrets.token_urlsafe(48)
    proof.email_token_hash = hashlib.sha256(token.encode()).hexdigest()
    proof.email_expires_at = proof.expires_at
    link = settings.public_url + '/?company_proof=' + token
    verification_event_mail(
        db, proof, 'business_email', proof.mailbox, 'Confirm your business email',
        'Please confirm access to your business email for company verification.\n\n' + link
        + '\n\nSign in to the account that requested this check. The link remains valid for this verification attempt. '
        'Email access alone does not approve a company. Ignore this email if you did not request it.'
    )


def owner(user):
    if user.role != 'owner' or not user.email_verified:
        raise HTTPException(403, 'A verified account owner is required')
    if settings.environment == 'production' and not user.mfa_enabled:
        raise HTTPException(403, 'Enable authenticator MFA before verifying company authority')


class Start(BaseModel):
    domain: str = Field(min_length=4, max_length=253)
    business_email: str = Field(min_length=5, max_length=254)
    number_type: str = Field(pattern=r'^(Local|Mobile|TollFree)$')
    contact_phone: str = Field(pattern=r'^\+[1-9][0-9]{7,14}$')
    authority_confirmed: bool

    @field_validator('domain')
    @classmethod
    def domain_valid(cls, value):
        return canonical_domain(value)

    @field_validator('business_email')
    @classmethod
    def email_valid(cls, value):
        from email_validator import validate_email
        if not value.isascii() or value != value.strip():
            raise ValueError('Use an ASCII business email without invisible characters.')
        result = validate_email(value, check_deliverability=False)
        canonical_domain(result.domain)
        return result.normalized


@router.get('')
def status(user=Depends(current_user)):
    with DB() as db:
        proof = db.get(CompanyVerification, user.tenant_id)
        if not proof:
            return {'available': settings.company_verification_enabled, 'status': 'not_started',
                    'message': 'Protect your company by verifying its domain and a current director.'}
        prerequisites = proof.email_verified and proof.dns_verified and proof.registry_verified
        identity_retry = proof.status == 'held' and proof.reason in {'provider_uncertain', 'identity_configuration'}
        if proof.authority_verified:
            stage, stage_label = 5, 'Director identity verified'
        elif prerequisites:
            stage, stage_label = 4, ('Director identity needs attention' if proof.status == 'held' else 'Director identity required')
        elif proof.registry_verified:
            stage, stage_label = 3, 'Waiting for domain or business email verification'
        elif proof.email_verified or proof.dns_verified:
            stage, stage_label = 2, 'Checking company and domain'
        else:
            stage, stage_label = 1, 'Verification started'
        telephone_stage = json.loads(proof.provider_state or '{}').get('stage', 'not_started')
        if proof.status == 'verified' and telephone_stage == 'approved':
            stage, stage_label = 6, 'Telephone approval complete'
        return {'available': settings.company_verification_enabled, 'status': proof.status,
                'message': MESSAGES.get(proof.status, MESSAGES['pending']),
                'explanation': REASONS.get(proof.reason, ''), 'reason': proof.reason, 'domain': proof.domain,
                'stage': stage, 'stage_total': 6, 'stage_label': stage_label,
                'checks': {'business_email': proof.email_verified, 'dns': proof.dns_verified,
                           'company_register': proof.registry_verified, 'director_authority': proof.authority_verified},
                'dns_name': '_raeburn-connect.' + proof.domain,
                'dns_value': 'raeburn-connect=' + proof.dns_token,
                'identity_ready': prerequisites and (proof.status == 'pending' or identity_retry),
                'identity_retry': identity_retry,
                'retryable': proof.status == 'held' and proof.reason in {'registry_mismatch', 'provider_unavailable'},
                'telephone_status': telephone_stage}


@router.post('/start', dependencies=[Depends(csrf)])
def start(data: Start, user=Depends(current_user)):
    owner(user)
    if not settings.company_verification_enabled:
        raise HTTPException(503, 'Automated company verification is being configured')
    if not data.authority_confirmed:
        raise HTTPException(422, 'Confirm that you are a current director and may act for this company')
    if data.business_email.rsplit('@', 1)[1].lower() != data.domain:
        raise HTTPException(422, 'Your business email must exactly match your registered domain')
    rate_limit('company-start:' + user.tenant_id, 3)
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        number = tenant.registration_number.strip().upper()
        if not re.fullmatch(r'(?:[0-9]{8}|[A-Z]{2}[0-9]{6})', number):
            raise HTTPException(422, 'A valid eight-character UK company number is required')
        if not tenant.legal_name or not tenant.address:
            raise HTTPException(422, 'Submit your legal company profile first')
        consume_daily_budget(db, 'company-start:' + tenant.id, 3)
        old = db.get(CompanyVerification, tenant.id)
        if old and old.status in {'pending', 'verified'}:
            raise HTTPException(409, 'A verification already exists. Check its status or update your company profile to start a new claim.')
        if claim_domain_risky(db, number, data.domain, tenant.id):
            raise HTTPException(422, 'This domain resembles a protected business domain. Check the spelling; automatic activation is unavailable for this claim.')
        claim = db.scalar(select(VerifiedCompanyClaim).where(
            (VerifiedCompanyClaim.company_number == number) | (VerifiedCompanyClaim.domain == data.domain)))
        if claim and claim.tenant_id != tenant.id:
            raise HTTPException(409, 'This company or domain already has a verified account')
        proof = CompanyVerification(tenant_id=tenant.id, attempt=uid(), user_id=user.id,
            profile_hash=profile_hash(tenant), domain=data.domain, mailbox=data.business_email,
            company_number=number, number_type=data.number_type,
            encrypted_contact=encrypt({'phone': data.contact_phone}), dns_token=secrets.token_urlsafe(32),
            expires_at=now() + timedelta(days=7))
        if old:
            if old.identity_session:
                # Redact the abandoned check before discarding its reference.
                identity_client().v1.identity.verification_sessions.redact(old.identity_session)
            db.delete(old)
            db.flush()
        db.add(proof)
        tenant.status, tenant.bundle_sid, tenant.address_sid, tenant.bundle_type = 'pending', '', '', ''
        email_challenge(db, proof)
        verification_event_mail(
            db, proof, 'verification_started', user.email, 'Your company verification has started',
            'Thank you — we are checking your company. Confirm your business email and add the DNS TXT record shown in your account. '
            'A current director will then complete a secure identity check.'
        )
        db.add(Audit(tenant_id=tenant.id, actor=user.id, action='company.verification.started', detail=proof.attempt))
    return {'status': 'pending'}


class Token(BaseModel):
    token: str = Field(min_length=40, max_length=100)


@router.post('/confirm-email', dependencies=[Depends(csrf)])
def confirm_email(data: Token, user=Depends(current_user)):
    owner(user)
    rate_limit('company-token:' + user.id, 5)
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        proof = db.get(CompanyVerification, tenant.id)
        digest = hashlib.sha256(data.token.encode()).hexdigest()
        if (not proof or proof.user_id != user.id or proof.status != 'pending'
                or proof.profile_hash != profile_hash(tenant) or not proof.email_token_hash
                or not hmac.compare_digest(proof.email_token_hash, digest)
                or aware(proof.email_expires_at) < now()):
            raise HTTPException(400, 'This business email link is invalid or expired')
        proof.email_verified, proof.email_token_hash, proof.next_check_at = True, '', now()
        db.add(Audit(tenant_id=tenant.id, actor=user.id, action='company.email.verified'))
    return {'status': 'email_verified'}


@router.post('/resend-email', dependencies=[Depends(csrf)])
def resend_email(user=Depends(current_user)):
    owner(user)
    raise HTTPException(409, 'One business email verification message is sent per verification attempt. Start a new verification if the link has expired.')



@router.post('/retry', dependencies=[Depends(csrf)])
def retry(user=Depends(current_user)):
    owner(user)
    rate_limit('company-retry:' + user.id, 3)
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        proof = db.get(CompanyVerification, tenant.id)
        if (not proof or proof.user_id != user.id or proof.status != 'held'
                or proof.reason not in {'registry_mismatch', 'provider_unavailable'}
                or proof.profile_hash != profile_hash(tenant) or aware(proof.expires_at) < now()):
            raise HTTPException(409, 'This verification cannot be retried in place')
        proof.status, proof.reason, proof.next_check_at = 'pending', '', now()
        db.add(Audit(tenant_id=tenant.id, actor=user.id, action='company.verification.retry'))
    return {'status': 'pending'}



def consume_daily_budget(db, prefix, maximum):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    key = prefix + ':' + now().date().isoformat()
    db.execute(insert(RateBucket).values(key=key, count=0, expires_at=now() + timedelta(days=2))
               .on_conflict_do_nothing(index_elements=['key']))
    budget = db.scalar(select(RateBucket).where(RateBucket.key == key).with_for_update())
    if budget.count >= maximum:
        raise HTTPException(429, 'Today’s verification capacity is reached; try tomorrow')
    budget.count += 1


def stripe_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    converter = getattr(value, 'to_dict_recursive', None) or getattr(value, 'to_dict', None)
    return converter() if converter else dict(value)


def identity_client():
    if not settings.identity_key:
        raise HTTPException(503, 'Director identity verification is being configured')
    return stripe.StripeClient(settings.identity_key, max_network_retries=0,
                               http_client=stripe.HTTPXClient(timeout=6, allow_sync_methods=True))


@router.post('/identity', dependencies=[Depends(csrf)])
def identity(user=Depends(current_user)):
    owner(user)
    if not settings.company_verification_enabled:
        raise HTTPException(503, 'Automated company verification is being configured')
    rate_limit('company-identity:' + user.id, 2)
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        proof = db.get(CompanyVerification, tenant.id)
        retryable_identity_hold = proof and proof.status == 'held' and proof.reason in {'provider_uncertain', 'identity_configuration'}
        if (not proof or proof.user_id != user.id or (proof.status != 'pending' and not retryable_identity_hold)
                or proof.profile_hash != profile_hash(tenant) or aware(proof.expires_at) < now()
                or not all([proof.email_verified, proof.dns_verified, proof.registry_verified])):
            raise HTTPException(409, 'Complete company, DNS and business email checks first')
        if retryable_identity_hold:
            proof.status, proof.reason = 'pending', ''
        api = identity_client()
        if proof.identity_session:
            session = api.v1.identity.verification_sessions.retrieve(proof.identity_session)
        else:
            consume_daily_budget(db, 'company-identity-budget', settings.verification_daily_limit)
            try:
                # Reconcile a process crash after remote creation before local commit.
                sessions = api.v1.identity.verification_sessions.list({'limit': 100})
                candidates = [item for item in sessions.data
                              if stripe_dict(item.metadata).get('attempt') == proof.attempt
                              and stripe_dict(item.metadata).get('tenant_id') == tenant.id]
                if len(candidates) > 1 or (sessions.has_more and not candidates):
                    raise ValueError('provider_uncertain')
                session = candidates[0] if candidates else api.v1.identity.verification_sessions.create({
                    'type': 'document', 'options': {'document': {'require_matching_selfie': True, 'require_live_capture': True}},
                    'metadata': {'tenant_id': tenant.id, 'attempt': proof.attempt},
                    'client_reference_id': proof.attempt, 'return_url': settings.public_url + '/?company_identity=returned',
                }, {'idempotency_key': 'company-identity:' + proof.attempt})
            except Exception as error:
                definite_rejection = type(error).__name__ in {'PermissionError', 'AuthenticationError', 'InvalidRequestError'}
                proof.status = 'held'
                proof.reason = 'identity_configuration' if definite_rejection else 'provider_uncertain'
                notify_state(db, proof)
                detail = ('Director identity could not start because the Stripe Identity configuration or API-key permissions need attention.'
                          if definite_rejection else
                          'Director identity returned an uncertain provider result. Your verified company checks are preserved and this step can be retried safely.')
                return JSONResponse(status_code=503, content={'detail': detail})
            proof.identity_session = session.id
        metadata = stripe_dict(session.metadata)
        if (metadata.get('tenant_id') != tenant.id or metadata.get('attempt') != proof.attempt
                or (settings.environment == 'production' and not session.livemode)):
            proof.status, proof.reason = 'held', 'authority_mismatch'
            notify_state(db, proof)
            return JSONResponse(status_code=409, content={'detail':'Identity verification does not match this company application.'})
        proof.next_check_at = now()
        parsed = urlsplit(session.url or '')
        if parsed.scheme != 'https' or parsed.hostname != 'verify.stripe.com':
            raise HTTPException(409, 'Identity check is processing; return to your account shortly')
        return {'url': session.url}


@router.get('/identity-qr')
def identity_qr(user=Depends(current_user)):
    owner(user)
    rate_limit('company-identity-qr:' + user.id, 20)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with DB.begin() as db:
        tenant = db.get(Tenant, user.tenant_id)
        proof = db.get(CompanyVerification, tenant.id)
        if (not proof or proof.user_id != user.id or not proof.identity_session
                or proof.profile_hash != profile_hash(tenant)
                or not all([proof.email_verified, proof.dns_verified, proof.registry_verified])):
            raise HTTPException(409, 'Start the director identity check first')
        db.add(ActionToken(token_hash=token_hash, user_id=user.id, purpose='identity_handoff',
                           expires_at=now() + timedelta(minutes=10)))

    handoff_url = settings.public_url + '/api/company-verification/identity-mobile/' + token
    qr = qrcode.QRCode(version=None, box_size=8, border=4)
    qr.add_data(handoff_url)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)
    return Response(content=output.getvalue(), media_type='image/svg+xml',
                    headers={'Content-Disposition': 'inline; filename="director-identity.svg"'})


def identity_handoff(token, consume=False):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with DB.begin() as db:
        action = db.scalar(select(ActionToken).where(ActionToken.token_hash == token_hash).with_for_update())
        if (not action or action.purpose != 'identity_handoff' or action.used
                or aware(action.expires_at) < now()):
            raise HTTPException(410, 'This phone handoff has expired. Return to Raeburn Connect and scan a new QR code.')
        user = db.get(User, action.user_id)
        tenant = db.get(Tenant, user.tenant_id) if user else None
        proof = db.get(CompanyVerification, user.tenant_id) if user else None
        if (not user or not tenant or not proof or proof.user_id != user.id or not proof.identity_session
                or proof.profile_hash != profile_hash(tenant)
                or not all([proof.email_verified, proof.dns_verified, proof.registry_verified])):
            raise HTTPException(409, 'This director identity handoff is no longer valid.')
        if consume:
            action.used = True
        return proof.identity_session, proof.attempt, tenant.id


@router.get('/identity-mobile/{token}', response_class=HTMLResponse)
def identity_mobile(token: str):
    identity_handoff(token)
    safe_token = re.sub(r'[^A-Za-z0-9_-]', '', token)
    return HTMLResponse(f'''<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Continue director verification · Raeburn Connect</title><link rel="stylesheet" href="/static/style.css">
<script src="https://js.stripe.com/v3/"></script><script defer src="/static/identity-mobile.js"></script></head>
<body class="mobile-handoff"><main class="mobile-handoff-main"><section class="feature mobile-handoff-card" data-identity-token="{safe_token}">
<p class="eyebrow">SECURE DIRECTOR VERIFICATION</p><h1>Continue on this phone.</h1>
<p class="lead">Use Stripe's secure mobile verification window to photograph your identity document and complete the selfie check.</p>
<p><strong>For document photos, use your phone's rear camera where Stripe offers it.</strong> If the hosted page previously selected the wrong camera, this modal flow uses Stripe's supported browser integration instead.</p>
<button type="button" id="identity-modal-start">Start secure identity check</button>
<p id="identity-mobile-status" class="muted" role="status" aria-live="polite"></p>
<a id="identity-direct-fallback" class="identity-direct-fallback" hidden>Having camera trouble? Open Stripe directly in Safari/Chrome</a>
<p class="muted">Open this page in Safari or Chrome rather than an embedded camera preview. The handoff expires after 10 minutes and works once. Raeburn Connect never receives your document or selfie images.</p>
</section></main></body></html>''')


def mobile_identity_session(token):
    session_id, attempt, tenant_id = identity_handoff(token, consume=True)
    try:
        session = identity_client().v1.identity.verification_sessions.retrieve(session_id)
    except Exception:
        raise HTTPException(503, 'The director identity provider is temporarily unavailable. Return to Raeburn Connect and try again.') from None

    metadata = stripe_dict(session.metadata)
    if (metadata.get('tenant_id') != tenant_id or metadata.get('attempt') != attempt
            or (settings.environment == 'production' and not session.livemode)):
        raise HTTPException(409, 'Identity verification does not match this company application')

    parsed = urlsplit(session.url or '')
    if parsed.scheme != 'https' or parsed.hostname != 'verify.stripe.com':
        raise HTTPException(409, 'The director identity session is no longer available. Return to Raeburn Connect and retry.')
    return session


@router.post('/identity-mobile/{token}/modal')
def identity_mobile_modal(token: str):
    session = mobile_identity_session(token)
    client_secret = getattr(session, 'client_secret', '') or ''
    publishable_key = settings.stripe_publishable_key
    if publishable_key and not publishable_key.startswith(('pk_live_', 'pk_test_')):
        raise HTTPException(503, 'Stripe Identity browser configuration is invalid.')
    if not client_secret or not publishable_key:
        return {'mode': 'redirect', 'redirect_url': session.url}
    return {'mode': 'modal', 'client_secret': client_secret,
            'publishable_key': publishable_key, 'redirect_url': session.url}


@router.post('/identity-mobile/{token}/continue')
def identity_mobile_continue(token: str):
    session = mobile_identity_session(token)
    return RedirectResponse(session.url, status_code=303)


def registry(tenant):
    number = tenant.registration_number.strip().upper()
    with httpx.Client(timeout=4, follow_redirects=False, trust_env=False) as api:
        response = api.get('https://api.company-information.service.gov.uk/company/' + number,
                           auth=(settings.companies_house_key, ''))
        if response.status_code == 404:
            raise ValueError('registry_ineligible')
        response.raise_for_status()
        company = response.json()
        response = api.get('https://api.company-information.service.gov.uk/company/' + number + '/officers',
                           params={'items_per_page': 100}, auth=(settings.companies_house_key, ''))
        response.raise_for_status()
        officers = response.json()
    if officers.get('total_results', 0) > len(officers.get('items', [])):
        raise ValueError('registry_ineligible')
    return company, officers.get('items', [])


def norm(value):
    # No transliteration or fuzzy matching for legal identities.
    return ''.join(c for c in unicodedata.normalize('NFC', value).casefold() if c.isalnum())


def registered_address(company):
    address = company.get('registered_office_address') or {}
    return ', '.join(str(address.get(key, '')) for key in
                     ('premises', 'address_line_1', 'address_line_2', 'locality', 'region', 'country', 'postal_code') if address.get(key))


def registry_match(tenant, company):
    if (company.get('company_number') != tenant.registration_number.strip().upper()
            or company.get('company_status') != 'active' or company.get('type') not in {'ltd', 'plc', 'private-limited-guarant-nsc'}
            or company.get('company_status_detail') or company.get('has_insolvency_history') or company.get('has_been_liquidated')
            or company.get('registered_office_is_in_dispute') or company.get('undeliverable_registered_office_address')):
        raise ValueError('registry_ineligible')
    if norm(tenant.legal_name) != norm(company.get('company_name', '')):
        raise ValueError('registry_mismatch')
    address = registered_address(company)
    # Formatting/commas are harmless; words, numbers and postcode must match.
    def tokens(value):
        return Counter(re.findall(r'[\w]+', unicodedata.normalize('NFC', value).casefold()))
    if tokens(tenant.address) != tokens(address):
        raise ValueError('registry_mismatch')
    if not company.get('registered_office_address', {}).get('postal_code') or re.search(r'\bpo\s*box\b', address, re.I):
        raise ValueError('registry_ineligible')


def txt_matches(proof):
    resolver = dns.resolver.Resolver()
    resolver.timeout, resolver.lifetime = 2, 4
    try:
        records = resolver.resolve('_raeburn-connect.' + proof.domain + '.', 'TXT', search=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False
    expected = ('raeburn-connect=' + proof.dns_token).encode()
    return any(hmac.compare_digest(b''.join(record.strings), expected) for record in records)


def director_match(report, officers, dob):
    report = stripe_dict(report)
    doc, selfie = stripe_dict(report.get('document')), stripe_dict(report.get('selfie'))
    if doc.get('status') != 'verified' or selfie.get('status') != 'verified':
        return False
    dob = dob or {}
    if not doc.get('first_name') or not doc.get('last_name') or not dob.get('month') or not dob.get('year'):
        return False
    target = norm(doc['last_name'] + doc['first_name'])
    matches = []
    for officer in officers:
        birth = officer.get('date_of_birth') or {}
        name = officer.get('name', '')
        if (officer.get('officer_role') == 'director' and not officer.get('resigned_on') and ',' in name
                and norm(name) == target and birth.get('month') == dob['month'] and birth.get('year') == dob['year']):
            matches.append(officer)
    return len(matches) == 1


def reconcile_identity(proof, officers):
    if not proof.identity_session:
        return False
    api = identity_client()
    session = api.v1.identity.verification_sessions.retrieve(
        proof.identity_session, {'expand': ['verified_outputs.dob']})
    expected_live = settings.environment == 'production'
    metadata = stripe_dict(session.metadata)
    options = stripe_dict(session.options)
    document_options = stripe_dict(options.get('document'))
    if (metadata.get('tenant_id') != proof.tenant_id or metadata.get('attempt') != proof.attempt
            or (expected_live and not session.livemode) or session.type != 'document'
            or not document_options.get('require_matching_selfie')
            or not document_options.get('require_live_capture')):
        raise ValueError('authority_mismatch')
    if session.status != 'verified':
        return False
    if not session.last_verification_report:
        raise ValueError('authority_mismatch')
    report = api.v1.identity.verification_reports.retrieve(session.last_verification_report)
    report = stripe_dict(report)
    if report.get('verification_session') != proof.identity_session or (expected_live and not report.get('livemode')):
        raise ValueError('authority_mismatch')
    verified_outputs = stripe_dict(session.verified_outputs)
    if not director_match(report, officers, stripe_dict(verified_outputs.get('dob'))):
        raise ValueError('authority_mismatch')
    # Only retain contact names needed by the telecom provider, encrypted.
    contact = decrypt(proof.encrypted_contact)
    contact.update(first_name=report['document']['first_name'], last_name=report['document']['last_name'])
    proof.encrypted_contact = encrypt(contact)
    return True


def notify_state(db, proof):
    # Progress, holds, retries and provider polling are intentionally UI-only.
    # Verification emails are emitted only by the four named event transitions.
    stage = json.loads(proof.provider_state or '{}').get('stage', '')
    proof.last_notified = proof.status + ':' + proof.reason + ':' + stage


def invalidate(db, tenant):
    proof = db.get(CompanyVerification, tenant.id)
    if proof:
        proof.status, proof.reason, proof.email_token_hash = 'invalidated', '', ''
        proof.email_verified = proof.dns_verified = proof.registry_verified = proof.authority_verified = False
        proof.encrypted_contact = ''
        db.add(Audit(tenant_id=tenant.id, actor='system', action='company.verification.invalidated'))


def cleanup_one():
    if not settings.identity_key:
        return False
    with DB() as db:
        tid = db.scalar(select(CompanyVerification.tenant_id).where(
            CompanyVerification.status.in_(['held', 'expired', 'invalidated']),
            CompanyVerification.created_at < now() - timedelta(days=30),
            CompanyVerification.next_check_at <= now(),
            (CompanyVerification.identity_session != '') | (CompanyVerification.encrypted_contact != ''),
        ).limit(1))
    if not tid:
        return False
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == tid).with_for_update(skip_locked=True))
        if not tenant:
            return False
        proof = db.get(CompanyVerification, tid)
        proof.next_check_at = now() + timedelta(hours=24)
        if proof.status not in {'held', 'expired', 'invalidated'}:
            return False
        try:
            if proof.identity_session:
                api = identity_client()
                session = api.v1.identity.verification_sessions.retrieve(proof.identity_session)
                if session.status not in {'requires_input', 'verified', 'canceled'}:
                    return True
                api.v1.identity.verification_sessions.redact(proof.identity_session)
            proof.identity_session, proof.encrypted_contact, proof.email_token_hash = '', '', ''
            db.add(Audit(tenant_id=tid, actor='worker', action='company.identity.redaction.requested'))
        except Exception:
            pass  # Keep the reference for tomorrow's bounded cleanup retry.
    return True


def verification_one():
    if not settings.company_verification_enabled:
        return False
    with DB() as db:
        tenant_id = db.scalar(select(CompanyVerification.tenant_id).where(
            CompanyVerification.status.in_(['pending', 'verified']), CompanyVerification.next_check_at <= now()
        ).order_by(CompanyVerification.next_check_at).limit(1))
    if not tenant_id:
        return False
    try:
        with DB.begin() as db:
            tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update(skip_locked=True))
            if not tenant:
                return False
            proof = db.get(CompanyVerification, tenant_id)
            if proof.status not in {'pending', 'verified'} or aware(proof.next_check_at) > now():
                return False
            proof.next_check_at = now() + timedelta(minutes=10)
            if proof.profile_hash != profile_hash(tenant):
                invalidate(db, tenant)
                tenant.status = 'pending'
                return True
            if proof.status != 'verified' and aware(proof.expires_at) < now():
                proof.status = 'expired'
                tenant.status = 'pending'
                notify_state(db, proof)
                return True
            try:
                if claim_domain_risky(db, proof.company_number, proof.domain, tenant_id):
                    raise ValueError('domain_risk')
                company, officers = registry(tenant)
                registry_match(tenant, company)
                proof.registry_verified = True
                proof.dns_verified = txt_matches(proof)
                proof.authority_verified = reconcile_identity(proof, officers)
                proof.reason = ''
                user = db.get(User, proof.user_id)
                if proof.email_verified and proof.dns_verified and proof.registry_verified:
                    verification_event_mail(
                        db, proof, 'company_verified', user.email, 'Your company and domain are verified',
                        'Your business email, domain control and Companies House company details are verified. '
                        'Complete the secure director identity check in your Raeburn Connect account to continue.'
                    )
                if proof.authority_verified:
                    verification_event_mail(
                        db, proof, 'director_verified', user.email, 'Your director identity is verified',
                        'Your director identity has been securely verified and matched to a current Companies House director. '
                        'Your company verification is complete.'
                    )
                if all([proof.email_verified, proof.dns_verified, proof.registry_verified, proof.authority_verified]):
                    claim = db.scalar(select(VerifiedCompanyClaim).where(
                        (VerifiedCompanyClaim.company_number == proof.company_number) | (VerifiedCompanyClaim.domain == proof.domain)
                        | (VerifiedCompanyClaim.tenant_id == tenant_id)))
                    if claim and (claim.tenant_id != tenant_id or claim.company_number != proof.company_number):
                        raise ValueError('claim_conflict')
                    if not claim:
                        db.add(VerifiedCompanyClaim(company_number=proof.company_number, domain=proof.domain, tenant_id=tenant_id))
                        db.flush()
                    else:
                        claim.domain = proof.domain
                    proof.status = 'verified'
                    proof.next_check_at = now() + timedelta(hours=24)
                    if settings.verification_auto_twilio:
                        from .regulatory_automation import advance
                        advance(db, tenant, proof, company)
                        # Poll provider approval frequently, then revalidate daily.
                        if tenant.status != 'approved':
                            proof.next_check_at = now() + timedelta(minutes=10)
                else:
                    proof.status = 'pending'
                    if tenant.status == 'approved':
                        tenant.status = 'pending'
                # Verification progress is shown in-product; no reminder emails are sent.
                proof.reminders = 0
                notify_state(db, proof)
            except ValueError as error:
                code = str(error)
                proof.status, proof.reason = 'held', code if code in REASONS else 'provider_requirements'
                tenant.status = 'pending'
                notify_state(db, proof)
            except Exception:
                # No raw provider errors, personal data or credentials in logs/UI.
                proof.reason = 'provider_unavailable'
                tenant.status = 'pending'
                notify_state(db, proof)
    except IntegrityError:
        with DB.begin() as db:
            proof = db.get(CompanyVerification, tenant_id)
            proof.status, proof.reason = 'held', 'claim_conflict'
            db.get(Tenant, tenant_id).status = 'pending'
            notify_state(db, proof)
    return True
