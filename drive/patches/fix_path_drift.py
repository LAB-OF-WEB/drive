"""
Reusable Drive Path Repair patch.

Fixes filename/path mismatches between `tabDrive File` and the files on disk,
using the DB folder tree as the single source of truth. Runs automatically on
`bench migrate` for ANY site with the drive app installed.

The runtime path-drift guard (in drive/utils/files.py and drive_file.py) stays
as the scheduled/defensive job; this patch repairs historical data one time.

It NEVER deletes anything. Review the plan on a live site first with:

    bench --site YOUR_SITE execute drive.utils.repair_paths.audit

IMPORTANT: This patch must NEVER abort `bench migrate`. Any per-file problem is
handled inside the repair engine; unexpected top-level errors are logged here
and swallowed so the migration always continues.
"""

import frappe

from drive.utils.repair_paths import run


def execute():
    try:
        stats = run(dry_run=False)
        if stats.get("missing"):
            frappe.log_error(
                f"Drive path repair: {stats['missing']} file(s) had no copy on disk "
                "and need to be re-uploaded",
                title="Drive Path Repair",
            )
        if stats.get("ambiguous"):
            frappe.log_error(
                f"Drive path repair: {stats['ambiguous']} file(s) had a same-named "
                "off-branch file and were NOT auto-moved (review manually)",
                title="Drive Path Repair",
            )
    except Exception as e:
        frappe.log_error(
            f"Drive path repair aborted with an unexpected error: {e!r}",
            title="Drive Path Repair",
        )
    frappe.db.commit()