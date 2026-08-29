#!/usr/bin/env python
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from faker import Faker
from datetime import datetime, timedelta
from src.database import SessionLocal, engine
from src.models.db_models import Base, Invoice, Transaction
import random

fake = Faker()

def seed():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Create 20 invoices
    vendors = ["Acme Corp", "Stripe Inc", "Amazon Web Services", "Microsoft", "Google Cloud",
               "Netflix", "Spotify", "Uber", "Lyft", "Slack", "Zoom", "Atlassian", "Datadog",
               "New Relic", "Twilio", "SendGrid", "Mailchimp", "Shopify", "Salesforce", "HubSpot"]
    for i in range(20):
        vendor = random.choice(vendors)
        inv = Invoice(
            vendor=vendor,
            invoice_number=f"INV-{1000+i}",
            amount=round(random.uniform(50, 5000), 2),
            currency="USD",
            due_date=fake.date_between(start_date='-30d', end_date='+30d')
        )
        db.add(inv)
    db.commit()

    # Create transactions: for each invoice, create a matching transaction with slight variations
    invoices = db.query(Invoice).all()
    for inv in invoices:
        # match with high probability (0.8)
        if random.random() < 0.8:
            amount_var = inv.amount * random.uniform(0.98, 1.02)
            tx = Transaction(
                date=inv.due_date + timedelta(days=random.randint(-3, 3)),
                description=f"{inv.vendor} PAYMENT" if random.random() > 0.3 else f"{inv.vendor} INC",
                amount=round(amount_var, 2),
                currency="USD"
            )
            db.add(tx)
        else:
            # create a non-matching transaction
            tx = Transaction(
                date=fake.date_between(start_date='-30d', end_date='+30d'),
                description=fake.company() + " " + fake.word(),
                amount=round(random.uniform(10, 100), 2),
                currency="USD"
            )
            db.add(tx)
    # Add a few extra random transactions
    for _ in range(10):
        tx = Transaction(
            date=fake.date_between(start_date='-30d', end_date='+30d'),
            description=fake.company() + " " + fake.word(),
            amount=round(random.uniform(10, 1000), 2),
            currency="USD"
        )
        db.add(tx)
    db.commit()
    print("Seeded database with 20 invoices and random transactions.")

if __name__ == "__main__":
    seed()