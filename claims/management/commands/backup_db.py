"""Back up the SQLite database: `manage.py backup_db`.

Copies the live database to `backups/db-YYYYMMDD-HHMMSS.sqlite3` and keeps the
10 most recent snapshots. SQLite only.

This is an on-server snapshot (handy before risky changes). Your real safety
net is downloading the file to your own computer now and then — Files tab →
db.sqlite3 (or any file under backups/) → Download.
"""

import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

KEEP = 10


class Command(BaseCommand):
    help = "Back up the SQLite database to backups/ with a timestamp"

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        if "sqlite3" not in db["ENGINE"]:
            raise CommandError("backup_db only supports SQLite databases.")
        src = Path(db["NAME"])
        if not src.exists():
            raise CommandError(f"Database file not found: {src}")

        backups = Path(settings.BASE_DIR) / "backups"
        backups.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backups / f"db-{stamp}.sqlite3"
        shutil.copy2(src, dest)

        # Prune old snapshots so backups never fill the disk.
        snapshots = sorted(backups.glob("db-*.sqlite3"))
        for old in snapshots[:-KEEP]:
            old.unlink()

        self.stdout.write(self.style.SUCCESS(f"Backed up to {dest}"))
