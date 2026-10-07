"""Security boundaries and the complete automated approval state machine."""

from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import pytest
from sqlalchemy import select
from app import company_verification as cv
from app import regulatory_automation as rc
from app.config import settings
from app.models import CompanyVerification, DB, EmailJob, Tenant, User, VerifiedCompanyClaim, now
from app.security import decrypt, encrypt
from test_flows import HEADERS, customer

COMPANY = {
    "company_number": "12345678",
    "company_name": "EXAMPLE LIMITED",
    "company_status": "active",
    "type": "ltd",
    "registered_office_address": {"address_line_1": "12 Example Street", "locality": "London", "postal_code": "SW1A 1AA"},
}
OFFICERS = [{"name": "SMITH, Alice Jane", "officer_role": "director", "date_of_birth": {"month": 4, "year": 1980}}]
REPORT = {
    "verification_session": "vs_proof",
    "livemode": True,
    "document": {"status": "verified", "first_name": "Alice Jane", "last_name": "Smith", "dob": {"month": 4, "year": 1980}},
    "selfie": {"status": "verified"},
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "company_verification_enabled", True)
    monkeypatch.setattr(settings, "companies_house_key", "registry-key")
    monkeypatch.setattr(settings, "identity_key", "rk_test_identity")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "verification_auto_twilio", False)
    monkeypatch.setattr(settings, "trusted_company_domains", "{}")
    monkeypatch.setattr(settings, "protected_domains", "theraeburngroup.com")
    monkeypatch.setattr(cv, "registry", lambda tenant: (COMPANY, OFFICERS))
    monkeypatch.setattr(cv, "txt_matches", lambda proof: True)


def applicant():
    client, tid = customer()
    with DB.begin() as db:
        db.scalar(select(User).where(User.tenant_id == tid)).email_verified = True
    assert (
        client.put(
            "/api/profile",
            json={"legal_name": "EXAMPLE LIMITED", "address": "12 Example Street, London, SW1A 1AA", "registration_number": "12345678"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    return client, tid


def begin(client, **overrides):
    data = {
        "domain": "example-business.co.uk",
        "business_email": "owner@example-business.co.uk",
        "number_type": "Local",
        "contact_phone": "+447700900001",
        "authority_confirmed": True,
    }
    data.update(overrides)
    return client.post("/api/company-verification/start", json=data, headers=HEADERS)


@pytest.mark.parametrize(
    "domain",
    [
        "theraeburngroup.com.evil.com",
        "www.example.com",
        "example.com@evil.com",
        "example.com:443",
        "example.com/path",
        " example.com",
        "example.com.",
        "xn--pple-43d.com",
        "аpple.com",
        "exam\u200bple.com",
        "co.uk",
        "foo.github.io",
        "gmail.com",
        "127.0.0.1",
        "example..com",
    ],
)
def test_unsafe_domain_inputs_rejected(domain):
    with pytest.raises(ValueError):
        cv.canonical_domain(domain)


@pytest.mark.parametrize("domain", ["theraebumgroup.com", "theraeburn-group.com", "theraeburngroup.net", "theraeburngroup-support.com"])
def test_lookalike_domains_never_auto_approve(domain):
    assert cv.risky_domain(domain, {"theraeburngroup.com"})


def test_exact_email_domain_and_account_bound_single_use_link(configured):
    c, tid = applicant()
    assert begin(c, business_email="owner@example-business.co.uk.evil.com").status_code == 422
    assert begin(c).status_code == 200
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        assert not proof.email_verified
        job = next(j for j in db.scalars(select(EmailJob)).all() if j.recipient == "owner@example-business.co.uk")
        token = decrypt(job.encrypted_payload)["body"].split("company_proof=")[1].split("\n")[0]
        assert token not in job.encrypted_payload
    other, _ = customer("other@example.com")
    with DB.begin() as db:
        db.scalar(select(User).where(User.email == "other@example.com")).email_verified = True
    assert other.post("/api/company-verification/confirm-email", json={"token": token}, headers=HEADERS).status_code == 400
    assert c.post("/api/company-verification/confirm-email", json={"token": token}, headers=HEADERS).status_code == 200
    assert c.post("/api/company-verification/confirm-email", json={"token": token}, headers=HEADERS).status_code == 400


def test_routine_pending_poll_does_not_email(configured):
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB.begin() as db:
        proof = db.get(CompanyVerification, tid)
        before = len(db.scalars(select(EmailJob)).all())
        proof.status, proof.reason, proof.provider_state = "pending", "", ""
        proof.last_notified = "held:registry_mismatch:"
        cv.notify_state(db, proof)
        after = len(db.scalars(select(EmailJob)).all())
        assert after == before
        assert proof.last_notified == "pending::"


def test_verification_email_events_are_one_time_per_attempt(configured):
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB.begin() as db:
        proof = db.get(CompanyVerification, tid)
        user = db.get(User, proof.user_id)
        # Start already queued the first two allowed company-verification events.
        jobs = [
            job for job in db.scalars(select(EmailJob)).all()
            if job.encrypted_payload and decrypt(job.encrypted_payload).get("category") == "company_verification"
        ]
        assert len(jobs) == 2
        assert not cv.verification_event_mail(db, proof, "business_email", proof.mailbox, "Duplicate", "Body")
        assert not cv.verification_event_mail(db, proof, "verification_started", user.email, "Duplicate", "Body")
        assert cv.verification_event_mail(db, proof, "company_verified", user.email, "Company verified", "Body")
        assert not cv.verification_event_mail(db, proof, "company_verified", user.email, "Duplicate", "Body")
        assert cv.verification_event_mail(db, proof, "director_verified", user.email, "Director verified", "Body")
        assert not cv.verification_event_mail(db, proof, "director_verified", user.email, "Duplicate", "Body")
        db.flush()
        jobs = [
            job for job in db.scalars(select(EmailJob)).all()
            if job.encrypted_payload and decrypt(job.encrypted_payload).get("category") == "company_verification"
        ]
        assert len(jobs) == 4


def test_mailbox_and_dns_alone_never_prove_authority(configured):
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB.begin() as db:
        db.get(CompanyVerification, tid).email_verified = True
    assert cv.verification_one()
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        assert proof.registry_verified and proof.dns_verified
        assert proof.status == "pending" and not proof.authority_verified
        assert db.get(Tenant, tid).status == "pending"
        with pytest.raises(Exception):
            cv.require_company_verified(db, db.get(Tenant, tid))


def test_fake_verified_browser_state_not_used(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200
    response = c.post("/api/company-verification/identity", headers=HEADERS)
    assert response.status_code == 409
    assert c.post("/api/company-verification/start", json={"status": "verified"}, headers=HEADERS).status_code == 422


def test_exact_director_identity_includes_dob_and_selfie():
    assert cv.director_match(REPORT, OFFICERS, REPORT["document"]["dob"])
    for officer in [
        dict(OFFICERS[0], resigned_on="2025-01-01"),
        dict(OFFICERS[0], officer_role="secretary"),
        dict(OFFICERS[0], date_of_birth={"month": 5, "year": 1980}),
    ]:
        assert not cv.director_match(REPORT, [officer], REPORT["document"]["dob"])
    assert not cv.director_match(dict(REPORT, selfie={"status": "unverified"}), OFFICERS, REPORT["document"]["dob"])
    assert not cv.director_match(REPORT, OFFICERS + OFFICERS, REPORT["document"]["dob"])


def test_live_identity_scope_and_provider_report_are_required(configured, monkeypatch):
    proof = NS(tenant_id="tenant", attempt="attempt", identity_session="vs_proof", encrypted_contact=encrypt({"phone": "+447700900001"}))
    session = NS(
        id="vs_proof",
        metadata={"tenant_id": "tenant", "attempt": "attempt"},
        livemode=True,
        type="document",
        status="verified",
        last_verification_report="vr_proof",
        verified_outputs={"dob": REPORT["document"]["dob"]},
        options={"document": {"require_matching_selfie": True, "require_live_capture": True}},
    )
    api = MagicMock()
    api.v1.identity.verification_sessions.retrieve.return_value = session
    api.v1.identity.verification_reports.retrieve.return_value = REPORT
    monkeypatch.setattr(cv, "identity_client", lambda: api)
    monkeypatch.setattr(settings, "environment", "production")
    assert cv.reconcile_identity(proof, OFFICERS)
    api.v1.identity.verification_sessions.retrieve.assert_called_with("vs_proof", {"expand": ["verified_outputs.dob"]})
    session.verified_outputs = {}
    with pytest.raises(ValueError):
        cv.reconcile_identity(proof, OFFICERS)
    session.verified_outputs = {"dob": REPORT["document"]["dob"]}
    session.livemode = False
    with pytest.raises(ValueError):
        cv.reconcile_identity(proof, OFFICERS)
    session.livemode = True
    session.metadata["attempt"] = "old-attempt"
    with pytest.raises(ValueError):
        cv.reconcile_identity(proof, OFFICERS)


def test_full_proof_creates_exclusive_claim_then_profile_change_invalidates(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200
    monkeypatch.setattr(cv, "reconcile_identity", lambda proof, officers: True)
    with DB.begin() as db:
        db.get(CompanyVerification, tid).email_verified = True
    assert cv.verification_one()
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        assert proof.status == "verified"
        assert db.get(VerifiedCompanyClaim, "12345678").tenant_id == tid
        cv.require_company_verified(db, db.get(Tenant, tid))
    assert (
        c.put(
            "/api/profile",
            json={"legal_name": "Another Company", "address": "12 Example Street, London, SW1A 1AA", "registration_number": "12345678"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        assert proof.status == "invalidated" and not proof.authority_verified
        with pytest.raises(Exception):
            cv.require_company_verified(db, db.get(Tenant, tid))


def test_registry_mismatch_is_held_and_not_retried(configured):
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB.begin() as db:
        db.get(Tenant, tid).legal_name = "Different Limited"
    assert cv.verification_one()
    with DB() as db:
        assert db.get(CompanyVerification, tid).status == "invalidated"
    assert not cv.verification_one()


def test_registry_outage_holds_activation_and_retries(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200

    def unavailable(tenant):
        raise RuntimeError("secret provider failure")

    monkeypatch.setattr(cv, "registry", unavailable)
    assert cv.verification_one()
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        assert proof.reason == "provider_unavailable" and proof.status == "pending"
        assert proof.next_check_at.replace(tzinfo=now().tzinfo) > now()
        assert db.get(Tenant, tid).status == "pending"
    assert "secret provider failure" not in c.get("/api/company-verification").text


def test_expired_proofs_do_not_approve(configured):
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB.begin() as db:
        db.get(CompanyVerification, tid).expires_at = now() - timedelta(seconds=1)
    assert cv.verification_one()
    with DB() as db:
        assert db.get(CompanyVerification, tid).status == "expired"


def test_independent_domain_anchor_overrides_only_exact_company(configured):
    settings.trusted_company_domains = '{"12345678":"theraeburngroup.com"}'
    try:
        with DB() as db:
            assert not cv.claim_domain_risky(db, "12345678", "theraeburngroup.com", "tid")
            assert cv.claim_domain_risky(db, "87654321", "theraeburngroup.com", "tid")
            assert cv.claim_domain_risky(db, "12345678", "theraeburngroup.net", "tid")
    finally:
        settings.trusted_company_domains = "{}"


def test_twilio_unknown_requirements_fail_closed():
    with pytest.raises(ValueError):
        rc.requirements(NS(requirements={"end_user": []}), {})
    with pytest.raises(ValueError):
        rc.requirements(
            NS(
                requirements={
                    "end_user": [{"type": "business", "fields": ["business_name"]}],
                    "supporting_document": [[{"accepted_documents": [{"type": "passport", "fields": ["files"]}]}]],
                }
            ),
            {"business_name": "Example"},
        )


def test_twilio_approval_reads_evidence_and_does_not_purchase_number(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200
    client = MagicMock()
    monkeypatch.setattr(rc, "tenant_client", lambda tenant: client)
    requirements = {
        "end_user": [{"type": "business", "fields": ["business_name"]}],
        "supporting_document": [[{"accepted_documents": [{"type": "business_address", "fields": ["address_sids"]}]}]],
    }
    regulation = NS(sid="RNproof", iso_country="GB", number_type="local", end_user_type="business", requirements=requirements)
    remote = client.numbers.v2.regulatory_compliance
    remote.regulations.list.return_value = [regulation]
    remote.regulations.return_value.fetch.return_value = regulation
    remote.bundles.return_value.fetch.return_value = NS(regulation_sid="RNproof", status="twilio-approved")
    remote.bundles.return_value.item_assignments.list.return_value = [NS(object_sid="ITend"), NS(object_sid="RDdoc")]
    remote.end_users.return_value.fetch.return_value = NS(type="business", attributes={"business_name": "EXAMPLE LIMITED"})
    remote.supporting_documents.return_value.fetch.return_value = NS(type="business_address", attributes={"address_sids": ["ADaddress"]})
    client.addresses.return_value.fetch.return_value = NS(**rc.address_fields(COMPANY))
    with DB.begin() as db:
        tenant = db.get(Tenant, tid)
        tenant.twilio_sid = "ACproof"
        proof = db.get(CompanyVerification, tid)
        proof.encrypted_contact = encrypt({"phone": "+447700900001", "first_name": "Alice", "last_name": "Smith"})
        proof.provider_state = (
            '{"contact_mobile_checked":true,"address":"ADaddress","end_user":"ITend","document":"RDdoc","bundle":"BUproof"}'
        )
        rc.advance(db, tenant, proof, COMPANY)
        assert tenant.status == "approved" and tenant.bundle_sid == "BUproof"
        client.incoming_phone_numbers.create.assert_not_called()
        remote.bundles.create.assert_not_called()


def test_existing_verified_claim_cannot_be_taken_over(configured):
    c, tid = applicant()
    with DB.begin() as db:
        db.add(VerifiedCompanyClaim(company_number="12345678", domain="another-business.com", tenant_id=tid))
    other, other_id = customer("other@example.com")
    with DB.begin() as db:
        db.scalar(select(User).where(User.tenant_id == other_id)).email_verified = True
        t = db.get(Tenant, other_id)
        t.legal_name = "EXAMPLE LIMITED"
        t.address = "12 Example Street, London, SW1A 1AA"
        t.registration_number = "12345678"
    assert begin(other).status_code == 409


def ready_for_identity(client, tid):
    assert begin(client).status_code == 200
    with DB.begin() as db:
        proof = db.get(CompanyVerification, tid)
        proof.email_verified = proof.dns_verified = proof.registry_verified = True
        return proof.attempt


def test_identity_creation_is_bound_capped_and_reused(configured, monkeypatch):
    c, tid = applicant()
    attempt = ready_for_identity(c, tid)
    api = MagicMock()
    api.v1.identity.verification_sessions.list.return_value = NS(data=[], has_more=False)
    session = NS(
        id="vs_proof", metadata={"tenant_id": tid, "attempt": attempt}, livemode=False, url="https://verify.stripe.com/start/vs_proof"
    )
    api.v1.identity.verification_sessions.create.return_value = session
    api.v1.identity.verification_sessions.retrieve.return_value = session
    monkeypatch.setattr(cv, "identity_client", lambda: api)
    assert c.post("/api/company-verification/identity", headers=HEADERS).status_code == 200
    assert c.post("/api/company-verification/identity", headers=HEADERS).status_code == 200
    assert api.v1.identity.verification_sessions.create.call_count == 1
    call = api.v1.identity.verification_sessions.create.call_args
    assert call.args[0]["options"]["document"]["require_matching_selfie"]
    assert call.args[0]["options"]["document"]["require_live_capture"]
    assert call.args[1]["idempotency_key"].endswith(attempt)


def test_ambiguous_identity_creation_is_not_repeated(configured, monkeypatch):
    c, tid = applicant()
    ready_for_identity(c, tid)
    api = MagicMock()
    api.v1.identity.verification_sessions.list.return_value = NS(data=[], has_more=False)
    api.v1.identity.verification_sessions.create.side_effect = TimeoutError("private detail")
    monkeypatch.setattr(cv, "identity_client", lambda: api)
    assert c.post("/api/company-verification/identity", headers=HEADERS).status_code == 503
    assert c.post("/api/company-verification/identity", headers=HEADERS).status_code == 409
    assert api.v1.identity.verification_sessions.create.call_count == 1
    with DB() as db:
        assert db.get(CompanyVerification, tid).reason == "provider_uncertain"


def test_business_email_is_sent_once_per_attempt(configured):
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        old_hash = proof.email_token_hash
        assert proof.email_sends == 1
        assert proof.email_expires_at == proof.expires_at
    assert c.post("/api/company-verification/resend-email", headers=HEADERS).status_code == 409
    with DB() as db:
        proof = db.get(CompanyVerification, tid)
        assert proof.email_token_hash == old_hash
        assert proof.email_sends == 1


def test_pending_company_claim_cannot_deny_service_to_another_domain(configured):
    # Pending application domains are not added to the trusted protection set.
    c, tid = applicant()
    assert begin(c).status_code == 200
    with DB() as db:
        assert "example-business.co.uk" not in cv.protected_for(db, "another-tenant")


def test_current_proof_gate_rejects_outage_and_stale_checks(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200
    monkeypatch.setattr(cv, "reconcile_identity", lambda proof, officers: True)
    with DB.begin() as db:
        db.get(CompanyVerification, tid).email_verified = True
    cv.verification_one()
    with DB.begin() as db:
        proof = db.get(CompanyVerification, tid)
        proof.reason = "provider_unavailable"
        with pytest.raises(Exception):
            cv.require_company_verified(db, db.get(Tenant, tid))
        proof.reason = ""
        proof.next_check_at = now() - timedelta(hours=2)
        with pytest.raises(Exception):
            cv.require_company_verified(db, db.get(Tenant, tid))


def test_provider_pending_bundle_is_polled_without_resubmission(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200
    client = MagicMock()
    monkeypatch.setattr(rc, "tenant_client", lambda tenant: client)
    rules = {
        "end_user": [{"type": "business", "fields": ["business_name"]}],
        "supporting_document": [[{"accepted_documents": [{"type": "business_address", "fields": ["address_sids"]}]}]],
    }
    reg = NS(sid="RNproof", iso_country="GB", number_type="local", end_user_type="business", requirements=rules)
    remote = client.numbers.v2.regulatory_compliance
    remote.regulations.list.return_value = [reg]
    remote.regulations.return_value.fetch.return_value = reg
    remote.bundles.return_value.fetch.return_value = NS(regulation_sid="RNproof", status="pending-review")
    remote.bundles.return_value.item_assignments.list.return_value = [NS(object_sid="ITend"), NS(object_sid="RDdoc")]
    with DB.begin() as db:
        t = db.get(Tenant, tid)
        t.twilio_sid = "ACproof"
        v = db.get(CompanyVerification, tid)
        v.encrypted_contact = encrypt({"phone": "+447700900001", "first_name": "Alice", "last_name": "Smith"})
        v.provider_state = '{"contact_mobile_checked":true,"address":"ADaddress","end_user":"ITend","document":"RDdoc","bundle":"BUproof"}'
        rc.advance(db, t, v, COMPANY)
        assert t.status == "pending"
    remote.bundles.return_value.update.assert_not_called()
    remote.bundles.return_value.evaluations.create.assert_not_called()


def test_voip_contact_is_held_before_regulatory_submission(configured, monkeypatch):
    c, tid = applicant()
    assert begin(c).status_code == 200
    client = MagicMock()
    monkeypatch.setattr(rc, "tenant_client", lambda tenant: client)
    reg = NS(sid="RNproof", iso_country="GB", number_type="local", end_user_type="business")
    client.numbers.v2.regulatory_compliance.regulations.list.return_value = [reg]
    client.numbers.v2.regulatory_compliance.regulations.return_value.fetch.return_value = reg
    client.lookups.v2.phone_numbers.return_value.fetch.return_value = NS(
        valid=True, phone_number="+447700900001", line_type_intelligence={"type": "nonFixedVoip", "error_code": None}
    )
    with DB.begin() as db:
        tenant = db.get(Tenant, tid)
        tenant.twilio_sid = "ACproof"
        proof = db.get(CompanyVerification, tid)
        with pytest.raises(ValueError, match="provider_requirements"):
            rc.advance(db, tenant, proof, COMPANY)
        assert tenant.status == "pending"
    client.numbers.v2.regulatory_compliance.bundles.create.assert_not_called()
