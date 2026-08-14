"""
Drive Path Drift Repair - one-shot patch for a live site.

Fixes filename/path mismatches between the Drive File records (tabDrive File)
and the actual files on disk, using the DB tree as the single source of truth.

Run on the live site:

    bench --site YOUR_SITE execute drive.utils.repair_paths.run

What it does per active non-group file:
  1. Computes the canonical path from the parent_entity folder chain.
  2. If the stored path already matches the canonical path -> OK (no change).
  3. If the file exists at the canonical path -> only fix the DB path.
  4. If the file exists elsewhere on disk -> move it to the canonical path,
     then fix the DB path.
  5. If the file only exists in the team .trash -> restore it, fix DB path.
  6. Otherwise -> flag as MISSING (needs manual attention).
Also repairs team mismatches (file team must equal its parent folder's team).

It never deletes anything. Run it with --dry-run first to review the plan.
"""

import os
import shutil
from pathlib import Path

import frappe
from frappe.utils import cint

from drive.utils import get_home_folder
from drive.utils.files import FileManager

DEBUG = True


def log(msg):
    line = f"[repair_paths] {msg}"
    print(line)
    if DEBUG:
        frappe.logger().info(line)

SITE_FOLDER = FileManager().site_folder
SKIP_PARTS = {".trash", ".thumbnails", ".embeds"}


def _is_hidden(rel_path: Path) -> bool:
    return any(p.startswith(".") for p in rel_path.parts)


def _walk_disk() -> dict[Path, Path]:
    """Index every file on disk keyed by its relative path (excluding hidden)."""
    index = {}
    if not SITE_FOLDER.is_dir():
        return index
    for f in SITE_FOLDER.rglob("*"):
        if f.is_file():
            rel = f.relative_to(SITE_FOLDER)
            if not _is_hidden(rel):
                index[rel] = f
    return index


def _canonical_path_for(name) -> Path | None:
    """Recompute canonical path by walking the parent_entity chain to the root folder."""
    row = frappe.db.get_value(
        "Drive File", name, ["title", "path", "parent_entity", "is_group"], as_dict=True
    )
    if not row:
        return None

    chain = []
    cur_name = row.parent_entity
    guard = 0
    while cur_name and guard < 50:
        p = frappe.db.get_value(
            "Drive File", cur_name, ["title", "path", "parent_entity", "is_group"], as_dict=True
        )
        if not p:
            break
        chain.append(p)
        if cint(p.is_group) == 0 or not p.parent_entity:
            break
        cur_name = p.parent_entity
        guard += 1
    if not chain:
        return None

    chain.reverse()
    root = chain[0]
    base = (root["path"] or "").rstrip("/")
    if not base:
        return None
    parts = [r["title"] for r in chain[1:]] + [row["title"]]
    return Path(base) / Path(*parts)


def _score_path(candidate: Path, chain_titles: list[str]) -> int:
    """Score how well a candidate path matches the canonical folder chain."""
    cand_parts = {p for p in candidate.parts}
    return sum(1 for t in chain_titles if t in cand_parts)


def _find_best_on_disk(disk_index: dict[Path, Path], canonical: Path, title: str):
    """Locate a single best match for title on disk, or None if ambiguous."""
    matches = [rel for rel in disk_index if rel.name == title]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    chain_titles = [p for p in canonical.parts[:-1]]
    scored = sorted(matches, key=lambda m: (_score_path(m, chain_titles), len(m.parts)), reverse=True)
    if _score_path(scored[0], chain_titles) > _score_path(scored[1], chain_titles):
        return scored[0]
    return None


def _team_of_parent(name) -> str | None:
    parent = frappe.db.get_value("Drive File", name, "parent_entity")
    if parent:
        return frappe.db.get_value("Drive File", parent, "team")
    return None


def _move_file(src_rel: Path, canonical: Path) -> None:
    """Move a file on disk to its canonical location, creating parent dirs."""
    src = SITE_FOLDER / src_rel
    dst = SITE_FOLDER / canonical
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        frappe.log_error(
            message=f"Repair: destination exists, skipping move {src_rel} -> {canonical}",
            title="Drive Path Drift Repair",
        )
        return
    shutil.move(str(src), str(dst))


def _restore_from_trash(team: str, canonical: Path) -> bool:
    """Restore a file from the team's .trash to the canonical location."""
    root = get_home_folder(team)
    trash_dir = SITE_FOLDER / root["path"] / ".trash"
    if not trash_dir.is_dir():
        return False
    for f in trash_dir.rglob("*"):
        if f.is_file() and f.name == canonical.name:
            dst = SITE_FOLDER / canonical
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            return True
    return False


def run(dry_run=False):
    """Audit + repair every active non-group Drive File."""
    dry_run = bool(cint(dry_run))
    mgr = FileManager()
    disk_index = _walk_disk()

    rows = frappe.get_all("Drive File", filters={"is_active": 1, "is_group": 0}, fields=["name"])

    stats = {"ok": 0, "path_fixed": 0, "moved": 0, "restored": 0, "team_fixed": 0, "missing": 0}
    report = []

    log(f"START audit | active non-group files: {len(rows)} | dry_run={dry_run} | disk_index={len(disk_index)}")

    for r in rows:
        name = r["name"]
        row = frappe.db.get_value(
            "Drive File", name, ["title", "path", "parent_entity", "team"], as_dict=True
        )
        if not row:
            log(f"SKIP {name}: record vanished")
            continue

        canonical = _canonical_path_for(name)

        # 1. Team mismatch repair (DB tree is source of truth)
        parent_team = _team_of_parent(name)
        if parent_team and parent_team != row.team:
            report.append(f"team-fix   {name}  {row.team} -> {parent_team}")
            if not dry_run:
                frappe.db.set_value("Drive File", name, "team", parent_team)
                log(f"FIXED team {name}: {row.team} -> {parent_team}")
            stats["team_fixed"] += 1

        # 2. Path checks
        current = (row.path or "").rstrip("/")
        if canonical is None:
            report.append(f"missing    {name}  (no canonical path)")
            stats["missing"] += 1
            log(f"MISSING {name}: cannot compute canonical path")
            continue

        canonical_str = str(canonical)
        if current == canonical_str.rstrip("/"):
            stats["ok"] += 1
            continue

        canonical_full = SITE_FOLDER / canonical
        if canonical_full.is_file():
            report.append(f"path-fix   {name}  {current} -> {canonical_str}")
            if not dry_run:
                frappe.db.set_value("Drive File", name, "path", canonical_str)
                log(f"FIXED path {name}: {current} -> {canonical_str}")
            stats["path_fixed"] += 1
            continue

        match = _find_best_on_disk(disk_index, canonical, row["title"])
        if match is not None:
            report.append(f"move       {name}  {match} -> {canonical_str}")
            if not dry_run:
                _move_file(match, canonical)
                frappe.db.set_value("Drive File", name, "path", canonical_str)
                log(f"MOVED {name}: {match} -> {canonical_str}")
            stats["moved"] += 1
            continue

        if _restore_from_trash(row.team, canonical):
            report.append(f"restore    {name}  (.trash) -> {canonical_str}")
            if not dry_run:
                frappe.db.set_value("Drive File", name, "path", canonical_str)
                log(f"RESTORED {name}: .trash -> {canonical_str}")
            stats["restored"] += 1
            continue

        report.append(f"missing    {name}  {row['title']}  (no copy found)")
        stats["missing"] += 1
        log(f"MISSING {name} ({row['title']}): no copy found on disk or trash")

    if not dry_run:
        frappe.db.commit()

    print("=" * 80)
    print(f"Drive Path Drift Repair  (dry_run={dry_run})")
    print("=" * 80)
    for line in report:
        print(line)
    print("-" * 80)
    print(
        f"ok={stats['ok']} path_fixed={stats['path_fixed']} moved={stats['moved']} "
        f"restored={stats['restored']} team_fixed={stats['team_fixed']} missing={stats['missing']}"
    )
    print(f"TOTAL: {len(rows)} active non-group files")
    return stats


def verify():
    """Verify every active non-group file downloads after repair."""
    mgr = FileManager()
    rows = frappe.get_all("Drive File", filters={"is_active": 1, "is_group": 0}, fields=["name"])
    fail = 0
    log(f"VERIFY start | total files: {len(rows)}")
    for r in rows:
        try:
            buf = mgr.get_file(frappe.get_doc("Drive File", r["name"]))
            log(f"VERIFY ok   {r['name']} | {len(buf.getvalue())} bytes")
        except Exception as e:
            fail += 1
            log(f"VERIFY FAIL {r['name']} | {e}")
            print("FAIL:", r["name"], e)
    log(f"VERIFY done | total={len(rows)} failures={fail}")
    print(f"Total active: {len(rows)}, Failures: {fail}")
    return {"total": len(rows), "failures": fail}