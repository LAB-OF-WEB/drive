"""
Reusable Drive Path Repair patch.

Fixes filename/path mismatches between `tabDrive File` and the files on disk,
using the DB folder tree as the single source of truth. Runs automatically on
`bench migrate` for ANY site with the drive app installed.

The runtime path-drift guard (in drive/utils/files.py and drive_file.py) stays
as the scheduled/defensive job; this patch repairs historical data one time.

It NEVER deletes anything. Run `drive.utils.repair_paths.run` with dry_run=True
first if you want to review the plan on a live site before migrating.
"""

import frappe

from drive.utils.repair_paths import run


def execute():
    stats = run(dry_run=False)
    if stats["missing"]:
        frappe.log_error(
            f"Drive path repair: {stats['missing']} file(s) had no copy on disk "
            "and need to be re-uploaded",
            title="Drive Path Repair",
        )
    frappe.db.commit()