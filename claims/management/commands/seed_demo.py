"""Seed the database with demo data for local development: `manage.py seed_demo`."""

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from claims.models import BillingItem, Claim, EmailLog, Reminder, Survey

VEHICLES = [
    ("ECB-101", "Toyota Corolla Hybrid"),
    ("ECB-214", "Mercedes-Benz Vito"),
    ("ECB-330", "Kia Niro"),
    ("ECB-407", "Toyota Prius+"),
    ("ECB-512", "Volkswagen Caddy"),
]
DRIVERS = ["Karl Borg", "Maria Vella", "Josef Camilleri", "Amy Farrugia", "Luca Grech"]
LOCATIONS = [
    "Triq ix-Xatt, Gzira",
    "Aldo Moro Road, Marsa",
    "Tower Road, Sliema",
    "Valletta Road, Luqa",
    "Coast Road, Bahar ic-Caghaq",
]
INSURERS = ["Mapfre Middlesea", "GasanMamo", "Atlas Insurance", "Elmo Insurance"]


class Command(BaseCommand):
    help = "Create demo users and claims for local development"

    def handle(self, *args, **options):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={"first_name": "Demo", "last_name": "Staff", "is_staff": True},
        )
        if created:
            user.set_password("demo1234")
            user.save()
            self.stdout.write("Created user demo / demo1234")

        now = timezone.now()
        statuses = list(Claim.Status.values)
        for i in range(12):
            reg, model = random.choice(VEHICLES)
            accident = now - timedelta(days=random.randint(1, 120))
            claim = Claim.objects.create(
                status=random.choice(statuses),
                vehicle_registration=reg,
                vehicle_make_model=model,
                driver_name=random.choice(DRIVERS),
                driver_phone=f"+356 79{random.randint(10000, 99999)}",
                accident_date=accident.date(),
                accident_time=accident.time().replace(microsecond=0),
                accident_location=random.choice(LOCATIONS),
                description="Third party changed lanes without indicating and clipped the front bumper.",
                fault=random.choice(list(Claim.Fault.values)),
                third_party_name="John Smith",
                third_party_vehicle="BMW 320i — ABC-123",
                third_party_insurer=random.choice(INSURERS),
                insurer=random.choice(INSURERS),
                policy_number=f"POL-{random.randint(100000, 999999)}",
                excess_amount=Decimal(random.choice(["250.00", "500.00", "750.00"])),
                created_by=user,
            )
            if claim.status != Claim.Status.DRAFT:
                claim.submitted_at = claim.created_at
                claim.save()
            if random.random() > 0.4:
                Survey.objects.create(
                    claim=claim,
                    surveyor_name="P. Attard",
                    surveyor_company="Attard Loss Adjusters",
                    status=random.choice(list(Survey.Status.values)),
                    scheduled_for=now + timedelta(days=random.randint(-10, 10)),
                    notes="Inspection at depot.",
                )
            if random.random() > 0.3:
                EmailLog.objects.create(
                    claim=claim,
                    direction=random.choice(["in", "out"]),
                    from_address="claims@ecabs.example",
                    to_address="claims@insurer.example",
                    subject=f"Claim {claim.reference} — accident report",
                    body="Please find attached the accident report and photos.",
                    sent_at=now - timedelta(days=random.randint(0, 30)),
                    logged_by=user,
                )
            if random.random() > 0.3:
                Reminder.objects.create(
                    claim=claim,
                    title="Chase insurer for update",
                    due_at=now + timedelta(days=random.randint(-5, 14)),
                    assigned_to=user,
                )
            for _ in range(random.randint(0, 3)):
                BillingItem.objects.create(
                    claim=claim,
                    description=random.choice(
                        ["Bumper repair", "Towing to depot", "Survey fee", "Rental replacement"]
                    ),
                    category=random.choice(list(BillingItem.Category.values)),
                    amount=Decimal(random.randint(80, 2500)),
                    status=random.choice(list(BillingItem.Status.values)),
                )
        self.stdout.write(self.style.SUCCESS("Seeded 12 demo claims."))
