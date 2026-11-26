import os
import sys

from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.database import engine, create_db_and_tables
from app.core.security import hash_password
from app.models.models import StaffUser
from app.schemas.schemas import BootstrapHeadAttorneyCreate


def bootstrap():
    create_db_and_tables()

    with Session(engine) as session:
        existing = session.exec(
            select(StaffUser).where(StaffUser.role == "head_attorney")
        ).first()
        if existing:
            print(f"A Head Attorney account already exists ({existing.email}). "
                  f"Nothing to do — use the API to create additional staff accounts.")
            return

        full_name = os.environ.get("BOOTSTRAP_HEAD_ATTORNEY_NAME")
        email = os.environ.get("BOOTSTRAP_HEAD_ATTORNEY_EMAIL")
        password = os.environ.get("BOOTSTRAP_HEAD_ATTORNEY_PASSWORD")

        if not all([full_name, email, password]):
            print(
                "Missing environment variables. Set all three before running:\n"
                "  BOOTSTRAP_HEAD_ATTORNEY_NAME\n"
                "  BOOTSTRAP_HEAD_ATTORNEY_EMAIL\n"
                "  BOOTSTRAP_HEAD_ATTORNEY_PASSWORD\n"
            )
            sys.exit(1)

        try:
            # Deliberately looser than StaffCreate: no @essu.edu.ph check,
            # so you can bootstrap with a personal email before a real
            # university address exists. Every account created afterward
            # via POST /staff still goes through StaffCreate, which DOES
            # enforce the domain restriction.
            validated = BootstrapHeadAttorneyCreate(
                full_name=full_name, email=email.strip().lower(), password=password
            )
        except ValidationError as e:
            print(f"Invalid bootstrap credentials:\n{e}")
            sys.exit(1)

        if not validated.email.endswith("@essu.edu.ph"):
            print(
                f"\u26a0\ufe0f  Using a non-ESSU email ({validated.email}) for this Head "
                "Attorney account. Fine for local development — replace it "
                "with a real @essu.edu.ph account before deploying (see the "
                "swap procedure below). New accounts created via POST /staff "
                "will still be REQUIRED to use @essu.edu.ph, regardless of "
                "this one."
            )

        staff = StaffUser(
            full_name=validated.full_name.strip(),
            email=validated.email,
            hashed_password=hash_password(validated.password),
            role="head_attorney",
            is_active=True,
            # Bootstrapped directly by whoever has server/DB access — there's
            # no other Head Attorney yet to have sent a verification code,
            # so this one account is marked verified immediately.
            is_email_verified=True,
        )
        session.add(staff)
        session.commit()

        print(f"Head Attorney account created: {validated.email}")
        print("They can log in immediately via POST /auth/login.")


if __name__ == "__main__":
    bootstrap()