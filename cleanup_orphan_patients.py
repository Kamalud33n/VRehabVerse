"""
cleanup_orphan_patients.py
---------------------------
Deletes old/legacy patients that have no responsible_therapist_id set
(created before per-therapist data isolation was added), along with
everything that hangs off them: sessions, session codes, live metrics,
exercise results, finger-tracking data, device telemetry, and reports
(both DB rows and the generated PDF files on disk).

VR devices that were linked to a deleted patient are NOT deleted — they
are just unassigned (patient_id set to NULL) so the physical/virtual
device record itself stays intact and can be re-linked to a new patient.

Nothing else (users, hospitals, exercises, settings, other patients that
DO have a responsible_therapist_id) is touched.

USAGE
-----
1) Preview only, nothing is deleted (always do this first):
     python cleanup_orphan_patients.py

2) Actually delete, after you've checked the preview:
     python cleanup_orphan_patients.py --confirm

Run this from inside the vr1/ project folder (same place as app.py),
so it can import database.py / models.py correctly.
"""

import argparse
import os
import sys

from database import get_db
from models import Patient, SessionModel, Report, VRDevice


def find_orphan_patients(db):
    return db.query(Patient).filter(Patient.responsible_therapist_id.is_(None)).all()


def preview(db, orphans) -> None:
    if not orphans:
        print("No orphan patients found (every patient already has a responsible_therapist_id). Nothing to do.")
        return

    print(f"Found {len(orphans)} orphan patient(s) with no responsible_therapist_id:\n")
    total_sessions = 0
    total_reports = 0
    total_devices = 0

    for p in orphans:
        session_count = db.query(SessionModel).filter(SessionModel.patient_id == p.id).count()
        report_count = db.query(Report).filter(Report.patient_id == p.id).count()
        device_count = db.query(VRDevice).filter(VRDevice.patient_id == p.id).count()
        total_sessions += session_count
        total_reports += report_count
        total_devices += device_count

        print(f"  - {p.id}  {p.name!r}  "
              f"({session_count} session(s), {report_count} report(s), "
              f"{device_count} device(s) to unlink)")

    print(f"\nTOTAL: {len(orphans)} patient(s), {total_sessions} session(s), "
          f"{total_reports} report(s) will be permanently deleted.")
    print(f"{total_devices} VR device(s) will be unlinked (kept, not deleted).")
    print("\nThis is a DRY RUN. Nothing has been deleted.")
    print("Re-run with --confirm to actually delete the above.")


def delete_orphans(db, orphans) -> None:
    deleted_patients = 0
    deleted_sessions = 0
    deleted_reports = 0
    unlinked_devices = 0
    removed_files = 0

    for p in orphans:
        sessions = db.query(SessionModel).filter(SessionModel.patient_id == p.id).all()
        deleted_sessions += len(sessions)

        reports = db.query(Report).filter(Report.patient_id == p.id).all()
        for r in reports:
            if r.file_path and os.path.exists(r.file_path):
                try:
                    os.remove(r.file_path)
                    removed_files += 1
                except OSError as exc:
                    print(f"  Warning: could not remove report file {r.file_path}: {exc}")
        deleted_reports += len(reports)

        devices = db.query(VRDevice).filter(VRDevice.patient_id == p.id).all()
        for d in devices:
            d.patient_id = None
        unlinked_devices += len(devices)

        # db.delete(patient) cascades to sessions/reports (and everything
        # that hangs off a session: codes, live_metrics, exercise_results,
        # finger_tracking, device_telemetry) because those relationships
        # are declared cascade="all, delete-orphan" in models.py.
        db.delete(p)
        deleted_patients += 1

    db.commit()

    print(f"Deleted {deleted_patients} patient(s), {deleted_sessions} session(s), "
          f"{deleted_reports} report(s) ({removed_files} PDF file(s) removed from disk).")
    print(f"Unlinked {unlinked_devices} VR device(s) (kept, just no longer assigned to a patient).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                         help="Actually perform the deletion. Without this flag, only a preview is shown.")
    args = parser.parse_args()

    with get_db() as db:
        orphans = find_orphan_patients(db)

        if not args.confirm:
            preview(db, orphans)
            return

        if not orphans:
            print("No orphan patients found. Nothing to do.")
            return

        preview(db, orphans)
        answer = input("\nType DELETE to confirm permanent deletion of the above: ").strip()
        if answer != "DELETE":
            print("Aborted. Nothing was deleted.")
            sys.exit(1)

        delete_orphans(db, orphans)


if __name__ == "__main__":
    main()