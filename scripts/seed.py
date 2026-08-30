#!/usr/bin/env python
"""
Database seeding script for the Reconcile Agent.

Populates the database with synthetic invoices and transactions,
and optionally creates a pending review match.

Usage:
    python -m poetry run python scripts/seed.py [--count N] [--drop] [--no-confirm]
    [--pending] [--pending-invoice ID]

Options:
    --count N           Number of invoices to generate (default: 20)
    --drop              Drop existing tables before seeding (default: False)
    --no-confirm        Skip confirmation prompt (use with caution)
    --seed SEED         Random seed for reproducible data (optional)
    --match-prob P      Probability (0-1) of a matching transaction per invoice (default: 0.8)
    --pending           Create a NEEDS_REVIEW match for the first invoice after seeding
    --pending-invoice ID  Invoice ID to create a pending match for (default: 1; requires --pending)

Examples:
    # Clean reset and seed 20 invoices with a pending match for invoice 1
    python -m poetry run python scripts/seed.py --drop --no-confirm --pending

    # Seed 50 invoices and create a pending match for invoice 5
    python -m poetry run python scripts/seed.py --count 50 --pending --pending-invoice 5

    # Add 10 invoices to existing data (no drop) and create a pending match
    python -m poetry run python scripts/seed.py --count 10 --pending
"""

import argparse
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to sys.path so that 'src' is found
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from faker import Faker

from src.database import SessionLocal, engine
from src.models.db_models import Base, Invoice, Match, Transaction

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Configuration constants
# ----------------------------------------------------------------------
DEFAULT_INVOICE_COUNT = 20
DEFAULT_MATCH_PROBABILITY = 0.8
VENDOR_POOL = [
    "Acme Corp", "Stripe Inc", "Amazon Web Services", "Microsoft", "Google Cloud",
    "Netflix", "Spotify", "Uber", "Lyft", "Slack", "Zoom", "Atlassian", "Datadog",
    "New Relic", "Twilio", "SendGrid", "Mailchimp", "Shopify", "Salesforce", "HubSpot",
]

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def _has_vendor_column() -> bool:
    """Check if the Transaction model has a 'vendor' column."""
    return hasattr(Transaction, "vendor")


def _get_unique_invoice_number(db, base=10000) -> str:
    """
    Generate a unique invoice number that doesn't exist in the database.
    Uses a timestamp + random suffix to ensure uniqueness.
    """
    existing_numbers = {inv.invoice_number for inv in db.query(Invoice.invoice_number).all()}

    for _ in range(100):
        timestamp = int(datetime.now().timestamp() * 1000) % 1000000
        suffix = random.randint(1000, 9999)
        inv_num = f"INV-{base + timestamp}-{suffix}"
        if inv_num not in existing_numbers:
            return inv_num

    raise RuntimeError("Could not generate a unique invoice number after 100 attempts")


def _create_pending_match(db, invoice_id: int) -> bool:
    """
    Create a NEEDS_REVIEW match for a given invoice.

    Returns:
        bool: True if created, False if invoice not found.
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        logger.warning(f"Invoice {invoice_id} not found – cannot create pending match.")
        return False

    match = Match(
        invoice_id=invoice.id,
        transaction_id=None,
        confidence_score=0.45,
        agent_decision="NEEDS_REVIEW",
        agent_notes="Demo: needs human approval (auto‑created by seed script)",
        created_at=datetime.utcnow(),
    )
    db.add(match)
    db.commit()
    logger.info(f"✅ Created pending match for invoice {invoice.id} (vendor: {invoice.vendor})")
    return True


# ----------------------------------------------------------------------
# Seeding function
# ----------------------------------------------------------------------
def seed(
    invoice_count: int = DEFAULT_INVOICE_COUNT,
    match_probability: float = DEFAULT_MATCH_PROBABILITY,
    drop_existing: bool = False,
    confirm: bool = True,
    seed: int | None = None,
    create_pending: bool = False,
    pending_invoice: int = 1,
) -> None:
    """
    Populate the database with synthetic invoices and transactions.

    Args:
        invoice_count: Number of invoices to create.
        match_probability: Probability (0.0-1.0) that an invoice gets a matching transaction.
        drop_existing: If True, drop all tables before seeding.
        confirm: If True, prompt for confirmation before dropping/clearing.
        seed: Optional random seed for reproducible results.
        create_pending: If True, create a NEEDS_REVIEW match for the first invoice.
        pending_invoice: Invoice ID to create a pending match for (if create_pending).
    """
    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)

    if drop_existing and confirm:
        response = input("⚠️  This will DROP all existing tables. Continue? [y/N]: ")
        if response.lower() != "y":
            logger.info("Seeding cancelled.")
            return

    # ------------------------------------------------------------------
    # 1. Reset database (if requested)
    # ------------------------------------------------------------------
    if drop_existing:
        logger.info("Dropping existing tables...")
        Base.metadata.drop_all(bind=engine)
        logger.info("Creating new tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database reset complete.")

    # ------------------------------------------------------------------
    # 2. Create session and seed data
    # ------------------------------------------------------------------
    db = SessionLocal()

    try:
        # 2a. Create invoices
        logger.info(f"Generating {invoice_count} invoices...")
        fake = Faker()
        invoices_created = 0

        for _ in range(invoice_count):
            vendor = random.choice(VENDOR_POOL)
            inv_num = _get_unique_invoice_number(db)
            inv = Invoice(
                vendor=vendor,
                invoice_number=inv_num,
                amount=round(random.uniform(50, 5000), 2),
                currency="USD",
                due_date=fake.date_between(start_date="-30d", end_date="+30d"),
            )
            db.add(inv)
            invoices_created += 1

        db.commit()
        logger.info(f"✅ Created {invoices_created} invoices.")

        # 2b. Fetch all invoices to create matching transactions
        invoices = db.query(Invoice).all()
        logger.info(f"Generating transactions for {len(invoices)} invoices...")

        transactions_created = 0
        has_vendor = _has_vendor_column()

        for inv in invoices:
            if random.random() < match_probability:
                amount_var = inv.amount * random.uniform(0.98, 1.02)
                tx_data = {
                    "date": inv.due_date + timedelta(days=random.randint(-3, 3)),
                    "description": (
                        f"{inv.vendor} PAYMENT"
                        if random.random() > 0.3
                        else f"{inv.vendor} INC"
                    ),
                    "amount": round(amount_var, 2),
                    "currency": "USD",
                }
                if has_vendor:
                    tx_data["vendor"] = inv.vendor
                db.add(Transaction(**tx_data))
                transactions_created += 1
            else:
                tx_data = {
                    "date": fake.date_between(start_date="-30d", end_date="+30d"),
                    "description": f"{fake.company()} {fake.word()}",
                    "amount": round(random.uniform(10, 100), 2),
                    "currency": "USD",
                }
                if has_vendor:
                    tx_data["vendor"] = random.choice(VENDOR_POOL)
                db.add(Transaction(**tx_data))
                transactions_created += 1

        # 2c. Add extra random transactions (noise)
        extra_count = random.randint(5, 15)
        logger.info(f"Adding {extra_count} extra random transactions...")
        for _ in range(extra_count):
            tx_data = {
                "date": fake.date_between(start_date="-30d", end_date="+30d"),
                "description": f"{fake.company()} {fake.word()}",
                "amount": round(random.uniform(10, 1000), 2),
                "currency": "USD",
            }
            if has_vendor:
                tx_data["vendor"] = random.choice(VENDOR_POOL)
            db.add(Transaction(**tx_data))
            transactions_created += 1

        db.commit()
        logger.info(f"✅ Created {transactions_created} transactions.")

        # 2d. Create pending match if requested
        if create_pending:
            logger.info("Creating pending review match...")
            # If pending_invoice is provided, try that; otherwise use the first invoice
            invoice_id_to_use = pending_invoice
            # If the invoice doesn't exist, try the first invoice
            if not db.query(Invoice).filter(Invoice.id == invoice_id_to_use).first():
                first_inv = db.query(Invoice).first()
                if first_inv:
                    invoice_id_to_use = first_inv.id
                else:
                    logger.warning("No invoices found – cannot create pending match.")
            if invoice_id_to_use:
                _create_pending_match(db, invoice_id_to_use)

        # 2e. Summary
        total_invoices = db.query(Invoice).count()
        total_transactions = db.query(Transaction).count()
        logger.info("=" * 50)
        logger.info("✅ Seeding completed successfully!")
        logger.info(f"   Total Invoices:     {total_invoices}")
        logger.info(f"   Total Transactions: {total_transactions}")
        if create_pending:
            logger.info("   Pending match:      Created (if invoice existed)")
        logger.info("=" * 50)

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Seeding failed: {e}")
        raise
    finally:
        db.close()


# ----------------------------------------------------------------------
# Command-line interface
# ----------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed the database with synthetic invoices and transactions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_INVOICE_COUNT,
        help=f"Number of invoices to create (default: {DEFAULT_INVOICE_COUNT})",
    )
    parser.add_argument(
        "--match-prob",
        type=float,
        default=DEFAULT_MATCH_PROBABILITY,
        help=f"Probability (0-1) of matching transaction per invoice (default: {DEFAULT_MATCH_PROBABILITY})",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop existing tables before seeding (destructive!)",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt (use with caution)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible data",
    )
    parser.add_argument(
        "--pending",
        action="store_true",
        help="Create a NEEDS_REVIEW match after seeding (for the first invoice or --pending-invoice)",
    )
    parser.add_argument(
        "--pending-invoice",
        type=int,
        default=1,
        help="Invoice ID to create a pending match for (default: 1; requires --pending)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    seed(
        invoice_count=args.count,
        match_probability=args.match_prob,
        drop_existing=args.drop,
        confirm=not args.no_confirm,
        seed=args.seed,
        create_pending=args.pending,
        pending_invoice=args.pending_invoice,
    )