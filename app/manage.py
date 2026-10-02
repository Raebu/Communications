"""Explicit schema/bootstrap and controlled operator commands."""

import argparse
import getpass
from sqlalchemy import select
from .models import Audit, Base, DB, Number, Order, Tenant, User, engine
from .security import hash_password


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("check-config")
    bootstrap = sub.add_parser("bootstrap-admin")
    bootstrap.add_argument("email")
    bootstrap.add_argument("--company", required=True)
    admin = sub.add_parser("promote-admin")
    admin.add_argument("email")
    password = sub.add_parser("reset-password")
    password.add_argument("email")
    retry = sub.add_parser("retry-order")
    retry.add_argument("id")
    binding = sub.add_parser("bind-sender")
    binding.add_argument("number_id")
    binding.add_argument("channel", choices=["whatsapp", "rcs"])
    binding.add_argument("sender")
    binding.add_argument("--service-sid", default="")
    binding.add_argument("--approval-reference", required=True)
    args = parser.parse_args()
    if args.command == "check-config":
        from .config import settings

        settings.validate()
        print("Configuration accepted")
        return
    if args.command == "init-db":
        Base.metadata.create_all(engine)
        return
    with DB.begin() as db:
        if args.command == "bind-sender":
            import re

            n = db.get(Number, args.number_id)
            if not n:
                raise SystemExit("Number not found")
            if args.channel == "whatsapp":
                if not re.fullmatch(r"whatsapp:\+[1-9]\d{7,14}", args.sender):
                    raise SystemExit("Invalid WhatsApp sender")
                n.whatsapp_sender, n.whatsapp = args.sender, "approved"
            else:
                if not args.sender.startswith("rcs:") or not re.fullmatch(r"MG[0-9a-fA-F]{32}", args.service_sid):
                    raise SystemExit("Invalid RCS sender/service")
                n.rcs_sender, n.rcs_service_sid, n.rcs = args.sender, args.service_sid, "approved"
            db.add(
                Audit(
                    tenant_id=n.tenant_id, actor="operator-cli", action="sender.bound", detail=args.channel + ":" + args.approval_reference
                )
            )
            print("Sender binding saved. Confirm subaccount ownership and provider approval before live use.")
            return
        if args.command == "bootstrap-admin":
            from email_validator import validate_email

            email = validate_email(args.email, check_deliverability=False).normalized.lower()
            if db.scalar(select(User).where(User.email == email)):
                raise SystemExit("Account already exists; use promote-admin instead")
            password = getpass.getpass("Administrator password (at least 12 characters): ")
            if len(password) < 12 or password != getpass.getpass("Confirm password: "):
                raise SystemExit("Password too short or confirmation does not match")
            tenant = Tenant(name=args.company)
            db.add(tenant)
            db.flush()
            user = User(tenant_id=tenant.id, email=email, password=hash_password(password), email_verified=True, platform_admin=True)
            db.add(user)
            db.flush()
            db.add(Audit(tenant_id=tenant.id, actor="operator-cli", action="admin.bootstrap", detail=user.id))
            print("Administrator created. Enrol authenticator MFA immediately after signing in.")
            return
        if args.command == "retry-order":
            order = db.get(Order, args.id)
            if not order or order.status != "review":
                raise SystemExit("Order must exist and be in review")
            order.status, order.error = "queued", ""
            db.add(Audit(tenant_id=order.tenant_id, actor="operator-cli", action="order.retry", detail=order.id))
            return
        user = db.scalar(select(User).where(User.email == args.email.lower()))
        if not user:
            raise SystemExit("Create customer account first")
        if args.command == "promote-admin":
            user.platform_admin = True
        else:
            password = getpass.getpass("New password (at least 12 characters): ")
            if len(password) < 12:
                raise SystemExit("Password too short")
            user.password = hash_password(password)
            from .models import Session

            for session in db.scalars(select(Session).where(Session.user_id == user.id)):
                db.delete(session)
        db.add(Audit(tenant_id=user.tenant_id, actor="operator-cli", action=args.command))


if __name__ == "__main__":
    main()
