"""Reconciled Twilio bundle workflow; query live requirements, never fabricate documents."""
import json
from .models import Audit
from .providers import create_subaccount, parent_client, tenant_client
from .security import decrypt

TYPES = {'Local': 'local', 'Mobile': 'mobile', 'TollFree': 'toll-free'}


def only_named(rows, name):
    if len(rows) > 100:
        raise ValueError('provider_requirements')
    matches = [row for row in rows if row.friendly_name == name]
    if len(matches) > 1:
        raise ValueError('provider_uncertain')
    return matches[0] if matches else None


def address_fields(company):
    address = company['registered_office_address']
    if not address.get('address_line_1') or not address.get('locality') or not address.get('postal_code'):
        raise ValueError('provider_requirements')
    return {'customer_name': company['company_name'],
            'street': ' '.join(str(address.get(x, '')) for x in ('premises', 'address_line_1')).strip(),
            'street_secondary': address.get('address_line_2', ''), 'city': address['locality'],
            'region': address.get('region') or address['locality'], 'postal_code': address['postal_code'],
            'iso_country': 'GB', 'auto_correct_address': False}


def regulation_for(client, proof):
    rc = client.numbers.v2.regulatory_compliance
    regs = rc.regulations.list(
        iso_country='GB',
        number_type=TYPES[proof.number_type],
        end_user_type='business',
        limit=2,
    )
    if len(regs) != 1:
        raise ValueError('provider_requirements')
    regulation = rc.regulations(regs[0].sid).fetch()
    if (regulation.iso_country != 'GB' or regulation.number_type != TYPES[proof.number_type]
            or regulation.end_user_type != 'business'):
        raise ValueError('provider_requirements')
    return rc, regulation


def end_user_attributes(proof, company):
    contact = decrypt(proof.encrypted_contact)
    return {
        'business_name': company['company_name'],
        'business_registration_identifier': 'UK:CRN',
        'business_registration_number': proof.company_number,
        'business_website': 'https://' + proof.domain,
        'first_name': contact['first_name'],
        'last_name': contact['last_name'],
        'phone_number': contact['phone'],
        'email': proof.mailbox,
        'business_identity': 'DIRECT_CUSTOMER',
        'is_subassigned': 'NO',
        'comments': '',
    }, contact


def requirements(regulation, attributes):
    if not isinstance(regulation.requirements, dict):
        raise ValueError('provider_requirements')
    end_users = regulation.requirements.get('end_user') or []
    if len(end_users) != 1 or end_users[0].get('type') != 'business':
        raise ValueError('provider_requirements')
    fields = end_users[0].get('fields') or []
    if not fields or any(field not in attributes for field in fields):
        raise ValueError('provider_requirements')
    # Support only the currently documented structured business address proof.
    # Additional document requirements are held, never filled with fake evidence.
    document_groups = regulation.requirements.get('supporting_document')
    if not isinstance(document_groups, list) or len(document_groups) != 1:
        raise ValueError('provider_requirements')
    for group in document_groups:
        if not isinstance(group, list) or len(group) != 1:
            raise ValueError('provider_requirements')
        options = group[0].get('accepted_documents') or []
        if not any(option.get('type') == 'business_address' and option.get('fields') == ['address_sids'] for option in options):
            raise ValueError('provider_requirements')
    return {field: attributes[field] for field in fields}



def preflight(tenant, proof, company):
    if not proof.authority_verified or proof.status != 'verified':
        raise ValueError('provider_requirements')
    client = parent_client()
    client.http_client.timeout = 5
    _, regulation = regulation_for(client, proof)
    attributes, contact = end_user_attributes(proof, company)
    required_attributes = requirements(regulation, attributes)
    address_fields(company)
    lookup = client.lookups.v2.phone_numbers(contact['phone']).fetch(fields='line_type_intelligence')
    intelligence = lookup.line_type_intelligence or {}
    if (not lookup.valid or lookup.phone_number != contact['phone']
            or intelligence.get('error_code') or intelligence.get('type') != 'mobile'):
        raise ValueError('provider_requirements')
    return {
        'ready': True,
        'number_type': proof.number_type,
        'regulation_sid': regulation.sid,
        'required_fields': sorted(required_attributes),
        'business_identity': required_attributes.get('business_identity'),
        'is_subassigned': required_attributes.get('is_subassigned'),
        'contact_mobile': True,
        'registered_address': True,
    }

def advance(db, tenant, proof, company):
    state = json.loads(proof.provider_state or '{}')
    name = 'raeburn-verification:' + proof.attempt
    try:
        if not tenant.twilio_sid:
            create_subaccount(tenant)
            state['stage'] = 'account_connected'
        else:
            client = tenant_client(tenant)
            client.http_client.timeout = 5
            rc, regulation = regulation_for(client, proof)
            attributes, contact = end_user_attributes(proof, company)
            if not state.get('contact_mobile_checked'):
                lookup = client.lookups.v2.phone_numbers(contact['phone']).fetch(fields='line_type_intelligence')
                intelligence = lookup.line_type_intelligence or {}
                if (not lookup.valid or lookup.phone_number != contact['phone']
                        or intelligence.get('error_code') or intelligence.get('type') != 'mobile'):
                    raise ValueError('provider_requirements')
                state['contact_mobile_checked'] = True
            attributes = requirements(regulation, attributes)
            current_requirements = json.dumps(regulation.requirements, sort_keys=True)
            if state.get('requirements') and state['requirements'] != current_requirements:
                raise ValueError('provider_requirements')
            state['requirements'] = current_requirements
            if not state.get('address'):
                row = only_named(client.addresses.list(friendly_name=name, limit=101), name)
                row = row or client.addresses.create(friendly_name=name, **address_fields(company))
                state.update(address=row.sid, stage='address_created')
            elif not state.get('end_user'):
                row = only_named(rc.end_users.list(limit=101), name)
                if row and (row.type != 'business' or row.attributes != attributes):
                    raise ValueError('provider_uncertain')
                row = row or rc.end_users.create(friendly_name=name, type='business', attributes=attributes)
                state.update(end_user=row.sid, stage='end_user_created')
            elif not state.get('document'):
                row = only_named(rc.supporting_documents.list(limit=101), name)
                row = row or rc.supporting_documents.create(friendly_name=name, type='business_address',
                    attributes={'address_sids': [state['address']]})
                state.update(document=row.sid, stage='address_proof_created')
            elif not state.get('bundle'):
                row = only_named(rc.bundles.list(friendly_name=name, limit=101), name)
                row = row or rc.bundles.create(friendly_name=name, email=proof.mailbox, regulation_sid=regulation.sid)
                if row.regulation_sid != regulation.sid:
                    raise ValueError('provider_uncertain')
                state.update(bundle=row.sid, stage='bundle_created')
            else:
                bundle_api = rc.bundles(state['bundle'])
                bundle = bundle_api.fetch()
                if bundle.regulation_sid != regulation.sid:
                    raise ValueError('provider_requirements')
                items = bundle_api.item_assignments.list(limit=101)
                if len(items) > 100:
                    raise ValueError('provider_requirements')
                actual = {item.object_sid for item in items}
                expected = {state['end_user'], state['document']}
                if actual - expected:
                    raise ValueError('provider_uncertain')
                missing = expected - actual
                if missing:
                    if bundle.status != 'draft':
                        raise ValueError('provider_uncertain')
                    bundle_api.item_assignments.create(object_sid=sorted(missing)[0])
                    state['stage'] = 'evidence_assigned'
                elif bundle.status == 'twilio-approved':
                    # Read provider-owned evidence again; don't trust browser SIDs.
                    end_user = rc.end_users(state['end_user']).fetch()
                    document = rc.supporting_documents(state['document']).fetch()
                    address = client.addresses(state['address']).fetch()
                    expected_address = address_fields(company)
                    if (end_user.type != 'business' or end_user.attributes != attributes
                            or document.type != 'business_address'
                            or document.attributes != {'address_sids': [state['address']]}
                            or any(str(getattr(address, key, '')) != str(value) for key, value in expected_address.items()
                                   if key != 'auto_correct_address')):
                        raise ValueError('provider_uncertain')
                    tenant.status, tenant.bundle_sid, tenant.address_sid, tenant.bundle_type = (
                        'approved', state['bundle'], state['address'], proof.number_type)
                    state['stage'] = 'approved'
                elif bundle.status in {'twilio-rejected', 'rejected'}:
                    raise ValueError('provider_rejected')
                elif bundle.status == 'draft':
                    evaluation = bundle_api.evaluations.create()
                    if evaluation.status != 'compliant':
                        raise ValueError('provider_requirements')
                    bundle_api.update(status='pending-review')
                    state['stage'] = 'pending_provider_approval'
                elif bundle.status in {'pending-review', 'in-review'}:
                    state['stage'] = 'pending_provider_approval'
                else:
                    raise ValueError('provider_requirements')
        proof.provider_state = json.dumps(state)
        db.add(Audit(tenant_id=tenant.id, actor='worker', action='company.provider.progress', detail=state['stage']))
    except ValueError:
        proof.provider_state = json.dumps(state)
        raise
    except Exception:
        # Remote create/update has no idempotency contract. Do not retry an
        # uncertain mutation automatically, even if that delays activation.
        proof.provider_state = json.dumps(state)
        raise ValueError('provider_uncertain') from None
