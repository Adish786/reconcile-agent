#!/usr/bin/env python
"""
Database seeding script for the Reconcile Agent.

This script populates the database with synthetic invoices and transactions
for testing, development, and demonstration purposes.

Usage:
    python -m poetry run python scripts/seed.py [--count N] [--drop] [--no-confirm]

Options:
    --count N       Number of invoices to generate (default: 20)
    --drop          Drop existing tables before seeding (default: False)
    --no-confirm    Skip confirmation prompt (use with caution)
    --seed SEED     Random seed for reproducible data (optional)

Example:
    python -m poetry run python scripts/seed.py --count 50 --drop --seed 42
"""

import argparse
import logging
from pathlib import Path
import random
import sys
from datetime import datetime, timedelta
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
from faker import Faker

from src.database import SessionLocal, engine
from src.models.db_models import Base, Invoice, Transaction

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Configuration constants
# ----------------------------------------------------------------------
DEFAULT_INVOICE_COUNT = 20
DEFAULT_MATCH_PROBABILITY = 0.8  # 80% of invoices get a matching transaction
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


# ----------------------------------------------------------------------
# Seeding function
# ----------------------------------------------------------------------
def seed(
    invoice_count: int = DEFAULT_INVOICE_COUNT,
    match_probability: float = DEFAULT_MATCH_PROBABILITY,
    drop_existing: bool = False,
    confirm: bool = True,
    seed: int | None = None,
) -> None:
    """
    Populate the database with synthetic invoices and transactions.

    Args:
        invoice_count: Number of invoices to create.
        match_probability: Probability (0.0-1.0) that an invoice gets a matching transaction.
        drop_existing: If True, drop all tables before seeding.
        confirm: If True, prompt for confirmation before dropping/clearing.
        seed: Optional random seed for reproducible results.

    Raises:
        SystemExit: If user declines confirmation.
    """
    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)

    # Confirm destructive actions
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
        for i in range(invoice_count):
            vendor = random.choice(VENDOR_POOL)
            inv = Invoice(
                vendor=vendor,
                invoice_number=f"INV-{10000 + i}",
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
            # Decide whether to create a matching transaction
            if random.random() < match_probability:
                # Matching transaction (close to invoice amount, same vendor)
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
                tx = Transaction(**tx_data)
                db.add(tx)
                transactions_created += 1
            else:
                # Non‑matching transaction (random vendor, amount)
                tx_data = {
                    "date": fake.date_between(start_date="-30d", end_date="+30d"),
                    "description": f"{fake.company()} {fake.word()}",
                    "amount": round(random.uniform(10, 100), 2),
                    "currency": "USD",
                }
                if has_vendor:
                    tx_data["vendor"] = random.choice(VENDOR_POOL)
                tx = Transaction(**tx_data)
                db.add(tx)
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
            tx = Transaction(**tx_data)
            db.add(tx)
            transactions_created += 1

        db.commit()
        logger.info(f"✅ Created {transactions_created} transactions.")

        # 2d. Summary
        total_invoices = db.query(Invoice).count()
        total_transactions = db.query(Transaction).count()
        logger.info("=" * 50)
        logger.info("✅ Seeding completed successfully!")
        logger.info(f"   Total Invoices:     {total_invoices}")
        logger.info(f"   Total Transactions: {total_transactions}")
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
    """Parse command-line arguments."""
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
    return parser.parse_args()


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    args = parse_args()
    seed(
        invoice_count=args.count,
        match_probability=args.match_prob,
        drop_existing=args.drop,
        confirm=not args.no_confirm,
        seed=args.seed,
    )