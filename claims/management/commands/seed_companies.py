"""Seed the group companies used on invoices: `manage.py seed_companies`.

Details taken from the company letterheads. Run once after migrating;
re-running updates the existing rows in place.
"""

from django.core.management.base import BaseCommand

from claims.models import Company

COMPANIES = [
    {
        "name": "Vai Drive Co Ltd.",
        "address": "Triq Santu Wistin,\nPaceville, St Julians, Malta",
        "phone": "(356) 21 38 38 38",
        "email": "motorclaims@ecabs.com.mt",
        "website": "www.ecabs.com.mt",
        "vat_no": "MT 23602405",
        "bank_name": "Banif Bank",
        "iban": "MT40BNIF14502000000000714984101",
        "account_no": "714984101",
        "swift": "BNIFMTMT",
        "logo_static": "img/companies/vai.png",
        "order": 1,
    },
    {
        # eCabs and Fast Drop share the same (APS) bank account.
        "name": "eCabs Ltd",
        "address": "Triq Santu Wistin, Paceville,\nSt Julians STJ 3180, Malta",
        "phone": "(356) 2138 3838",
        "email": "motorclaims@ecabs.com.mt",
        "website": "ecabs.com.mt",
        "vat_no": "MT 23602405",
        "exo_number": "4270",
        "bank_name": "APS Bank",
        "iban": "MT55APSB77013000000039180120017",
        "account_no": "3918012001-7",
        "swift": "APSBMTMT",
        "logo_static": "img/companies/ecabs.png",
        "order": 2,
    },
    {
        "name": "Fast Drop",
        "address": "Triq Santu Wistin, Paceville,\nSt Julians STJ 3180, Malta",
        "phone": "(356) 2138 3838",
        "email": "motorclaims@ecabs.com.mt",
        "website": "ecabs.com.mt",
        "vat_no": "MT 23602405",
        "bank_name": "APS Bank",
        "iban": "MT55APSB77013000000039180120017",
        "account_no": "3918012001-7",
        "swift": "APSBMTMT",
        "logo_static": "img/companies/fastdrop.png",
        "order": 3,
    },
    {
        # Bank details for Fastdrop International not on its letterhead —
        # confirm and complete in Admin > Companies.
        "name": "Fastdrop International Ltd",
        "address": "S&T Building,\nTriq San Bernard,\nMarsa MRS 1330, Malta",
        "phone": "(356) 2138 3838",
        "email": "motorclaims@ecabs.com.mt",
        "website": "",
        "vat_no": "",
        "bank_name": "APS Bank",
        "iban": "",
        "account_no": "",
        "swift": "APSBMTMT",
        "logo_static": "img/companies/fastdrop.png",
        "order": 4,
    },
]


class Command(BaseCommand):
    help = "Create/update the group companies used on invoices"

    def handle(self, *args, **options):
        for data in COMPANIES:
            Company.objects.update_or_create(name=data["name"], defaults=data)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(COMPANIES)} companies."))
