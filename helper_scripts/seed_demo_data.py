#!/usr/bin/env python3
"""Seed plausible demo data for a showcase of the CMMS.

Fills the thin parts of a demo instance — job cards with real costs and
downtime, spare parts drawn against them, a tool store, and completed PPM
history — so Machine Reports, the HOD dashboard and the parts module have
something to show.

Every record it creates is recorded in a manifest, so the whole seed can be
removed again:

    python helper_scripts/seed_demo_data.py            # seed
    python helper_scripts/seed_demo_data.py --undo     # remove exactly what it made

It never touches equipment, users, workshops or departments — it only adds to
them. Take a backup first anyway: python manage.py backup_db
"""
import argparse
import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Equiper.settings")

import django
django.setup()

from django.db import transaction
from django.utils import timezone

from Inventory.models import Equipment, EquipmentDescription
from jobcard.models import SparePartUsed, jobcard
from parts_tools.models import (
    Accessories,
    AccessoriesManufacturer,
    Accessoriesname,
    Toolname,
    Tools,
    ToolsManufacturer,
)
from ppms.models import PPMSchedule
from users.models import UserProfile, UserSignature
from workshop.models import Workshop

MANIFEST = Path(__file__).resolve().parent.parent / "data" / "demo_seed_manifest.json"

SEED = 20260917
MONTHS_BACK = 14
N_JOBCARDS = 165

# ---------------------------------------------------------------- vocabulary

SPARE_PARTS = [
    ("Blood Pressure Cuff, Adult", 3200),
    ("SpO2 Finger Sensor, Reusable", 8500),
    ("ECG Electrodes, Pack of 50", 1450),
    ("Infusion Pump Giving Set", 650),
    ("Dialyser, High Flux", 2400),
    ("Blood Tubing Line Set", 1850),
    ("Suction Canister, 2L", 2100),
    ("Nebuliser Mask Kit", 780),
    ("Ventilator Bacterial Filter", 1250),
    ("Sealed Lead Acid Battery 12V 7Ah", 4600),
    ("Defibrillator Pads, Adult", 6900),
    ("Temperature Probe, Skin", 3400),
    ("Air Intake Filter", 950),
    ("Pressure Transducer", 12500),
    ("Mains Cable, IEC C13", 620),
    ("Flow Sensor, Proximal", 18900),
    ("O-Ring Seal Kit", 430),
    ("Oxygen Cell", 9800),
]

PART_MAKERS = ["Philips", "Fresenius Medical Care", "Mindray", "B. Braun", "Generic OEM"]

TOOLS = [
    ("Digital Multimeter", "Fluke Biomedical", "87V"),
    ("Electrical Safety Analyser", "Fluke Biomedical", "ESA615"),
    ("NIBP Simulator", "Fluke Biomedical", "CuffLink"),
    ("SpO2 Simulator", "Fluke Biomedical", "Index 2"),
    ("Defibrillator Analyser", "Datrend Systems", "Phase 3"),
    ("Infusion Pump Analyser", "Rigel Medical", "Multi-Flo"),
    ("Torque Screwdriver Set", "Gossen Metrawatt", "TS-40"),
    ("Digital Thermometer, Reference", "Fluke Biomedical", "1524"),
    ("Leakage Current Tester", "Rigel Medical", "288+"),
    ("Digital Pressure Meter", "Datrend Systems", "vPad-A1"),
]

REPAIR_JOBS = [
    ("Unit not powering on", "Replaced faulty mains fuse and inspected power board."),
    ("Intermittent display flicker", "Reseated display ribbon connector and secured harness."),
    ("Alarm sounding continuously", "Cleaned sensor port and recalibrated alarm threshold."),
    ("Pressure reading drifting", "Replaced pressure transducer and verified against reference."),
    ("Battery not holding charge", "Replaced sealed lead acid battery and ran discharge test."),
    ("Pump occlusion error", "Cleared line obstruction and replaced giving set."),
    ("SpO2 reading unstable", "Replaced finger sensor and confirmed against simulator."),
    ("Leaking fluid at connector", "Replaced O-ring seal kit and pressure tested."),
    ("Fan noisy on startup", "Cleaned air intake filter and lubricated fan bearing."),
    ("Screen unresponsive to touch", "Recalibrated touch panel and updated firmware."),
    ("Device failed electrical safety test", "Replaced mains cable, earth leakage now within limit."),
    ("Oxygen concentration reading low", "Replaced oxygen cell and ran two-point calibration."),
]

PPM_JOBS = [
    ("Scheduled preventive maintenance", "Performed PPM per checklist: cleaned, inspected, electrical safety passed."),
    ("Quarterly service", "Filters replaced, functional checks passed, safety test within limits."),
    ("Annual preventive maintenance", "Full service carried out, calibration verified, unit returned to service."),
]

CAL_JOBS = [
    ("Routine calibration", "Calibration performed against reference standard; within tolerance."),
    ("Post-repair verification", "Verified accuracy after repair; certificate issued."),
]

OTHER_JOBS = [
    ("User training on device operation", "Trained ward staff on correct operation and daily checks."),
    ("Relocation and commissioning", "Relocated unit, commissioned and safety tested in new ward."),
    ("Condemnation assessment", "Assessed for economic repair; report submitted to HOD."),
]

DECLINE_REASONS = [
    "Parts cost not supported by quotation — resubmit with supplier quote.",
    "Downtime hours do not match the ward's incident log. Please verify.",
    "Job card raised against the wrong serial number.",
]


def money(lo, hi, step=50):
    return Decimal(str(random.randrange(lo, hi, step)))


def working_time():
    start_h = random.randint(8, 14)
    start_m = random.choice([0, 15, 30, 45])
    dur = random.randint(25, 320)
    start = datetime(2000, 1, 1, start_h, start_m)
    end = start + timedelta(minutes=dur)
    if end.day != start.day:
        end = datetime(2000, 1, 1, 23, 45)
    return start.time(), end.time()


# ---------------------------------------------------------------- undo

def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def undo():
    man = load_manifest()
    if not man:
        print("No manifest found — nothing to undo.")
        return

    with transaction.atomic():
        n = SparePartUsed.objects.filter(id__in=man.get("spare_parts", [])).delete()[0]
        print(f"  spare parts used   removed {n}")
        n = jobcard.objects.filter(id__in=man.get("jobcards", [])).delete()[0]
        print(f"  job cards          removed {n}")
        n = Tools.objects.filter(id__in=man.get("tools", [])).delete()[0]
        print(f"  tools              removed {n}")
        n = Accessories.objects.filter(id__in=man.get("accessories", [])).delete()[0]
        print(f"  accessories        removed {n}")
        n = Toolname.objects.filter(id__in=man.get("toolnames", [])).delete()[0]
        print(f"  tool names         removed {n}")
        n = ToolsManufacturer.objects.filter(id__in=man.get("toolmakers", [])).delete()[0]
        print(f"  tool makers        removed {n}")
        n = Accessoriesname.objects.filter(id__in=man.get("accnames", [])).delete()[0]
        print(f"  accessory names    removed {n}")
        n = AccessoriesManufacturer.objects.filter(id__in=man.get("accmakers", [])).delete()[0]
        print(f"  accessory makers   removed {n}")

        n = PPMSchedule.objects.filter(id__in=man.get("ppm_created", [])).delete()[0]
        print(f"  PPM history        removed {n}")

        # Any schedules edited in place rather than created — put them back.
        reverted = 0
        for sid, prev in man.get("ppm_reverts", {}).items():
            reverted += PPMSchedule.objects.filter(id=sid).update(status=prev)
        print(f"  PPM schedules      reverted {reverted}")

    MANIFEST.unlink()
    print("\nSeed removed. Manifest deleted.")


# ---------------------------------------------------------------- seed

def seed():
    random.seed(SEED)
    man = {
        "accmakers": [], "accnames": [], "accessories": [],
        "toolmakers": [], "toolnames": [], "tools": [],
        "jobcards": [], "spare_parts": [], "ppm_reverts": {},
    }

    workshops = list(Workshop.objects.all())
    descriptions = list(EquipmentDescription.objects.all())
    equipment = list(
        Equipment.objects.select_related("department", "workshop").filter(active_status=True)
    )
    if not (workshops and descriptions and equipment):
        print("This instance has no workshops / descriptions / equipment to build on.")
        return

    techs = list(UserProfile.objects.filter(role="Tech").select_related("user", "workshop"))
    verifiers = list(UserProfile.objects.filter(role__in=["NIC", "HOD"]).select_related("user"))
    if not techs:
        print("No technologist accounts to attribute work to.")
        return

    signatures = [s.signature_data for s in UserSignature.objects.all() if s.signature_data]
    print(f"context: {len(equipment)} devices · {len(techs)} techs · "
          f"{len(signatures)} signatures on file")

    # ---- spare parts ----
    print("\nspare parts")
    makers = []
    for name in PART_MAKERS:
        m, created = AccessoriesManufacturer.objects.get_or_create(name=name)
        if created:
            man["accmakers"].append(str(m.id))
        makers.append(m)

    accessories = []
    for part_name, cost in SPARE_PARTS:
        an, created = Accessoriesname.objects.get_or_create(name=part_name)
        if created:
            man["accnames"].append(str(an.id))
        acc = Accessories.objects.create(
            name=an,
            manufacturer=random.choice(makers),
            equipment_description=random.choice(descriptions),
            stock_count=random.randint(45, 220),
            unit_cost=Decimal(str(cost)),
            workshop=random.choice(workshops),
            note="Demo stock",
        )
        man["accessories"].append(str(acc.id))
        accessories.append(acc)
    print(f"  created {len(accessories)} accessories")

    # ---- tools ----
    print("tools")
    made_tools = 0
    for tool_name, maker_name, model in TOOLS:
        tm, created = ToolsManufacturer.objects.get_or_create(name=maker_name)
        if created:
            man["toolmakers"].append(str(tm.id))
        tn, created = Toolname.objects.get_or_create(name=tool_name)
        if created:
            man["toolnames"].append(str(tn.id))
        t = Tools.objects.create(
            name=tn,
            manufacturer=tm,
            model=model,
            serial_number=f"TL-{random.randint(10000, 99999)}",
            workshop=random.choice(workshops),
        )
        man["tools"].append(str(t.id))
        made_tools += 1
    print(f"  created {made_tools} tools")

    # ---- job cards ----
    print("job cards")
    today = date.today()
    made = {"Approved": 0, "Waiting Approval": 0, "Declined": 0}
    parts_rows = 0

    for _ in range(N_JOBCARDS):
        eq = random.choice(equipment)
        if not eq.department:
            continue

        roll = random.random()
        if roll < 0.55:
            action, (desc, taken) = "Repair", random.choice(REPAIR_JOBS)
        elif roll < 0.85:
            action, (desc, taken) = "PPM", random.choice(PPM_JOBS)
        elif roll < 0.95:
            action, (desc, taken) = "Calibration", random.choice(CAL_JOBS)
        else:
            action, (desc, taken) = "Others", random.choice(OTHER_JOBS)

        s_roll = random.random()
        status = ("Approved" if s_roll < 0.80
                  else "Waiting Approval" if s_roll < 0.92 else "Declined")

        tech = random.choice(
            [t for t in techs if t.workshop_id == eq.workshop_id] or techs
        )
        started, completed = working_time()
        issued = today - timedelta(days=random.randint(1, MONTHS_BACK * 30))

        labor = money(500, 9000) if action != "Others" else money(0, 2500)
        additional = money(0, 6000) if random.random() < 0.3 else Decimal("0.00")

        jc = jobcard(
            department=eq.department,
            equipment=eq,
            workshop=eq.workshop or random.choice(workshops),
            priority_level=random.choices(
                ["Low", "Medium", "High", "Urgent"], [0.25, 0.42, 0.25, 0.08]
            )[0],
            action_taken=action,
            job_description=f"{desc}. {taken}",
            time_started=started,
            time_completed=completed,
            performed_by=tech.user,
            technician_signed_date=timezone.make_aware(
                datetime.combine(issued, completed)
            ),
            tech_signature=random.choice(signatures) if signatures else "",
            status=status,
            labor_cost=labor,
            additional_costs=additional,
            additional_costs_description=(
                "External service engineer call-out" if additional else None
            ),
            stock_deducted=(status == "Approved"),
        )
        if status == "Approved" and verifiers:
            v = random.choice(verifiers)
            jc.verified_by_nurse = v.user
            jc.nurse_name = v.user.get_full_name() or v.user.username
            jc.nurse_signed_date = timezone.make_aware(
                datetime.combine(issued, completed)
            ) + timedelta(hours=random.randint(1, 20))
            if signatures:
                jc.verified_signature = random.choice(signatures)
        elif status == "Declined":
            jc.decline_reason = random.choice(DECLINE_REASONS)

        jc.save()
        man["jobcards"].append(str(jc.id))
        made[status] += 1

        # spare parts, mostly on repairs
        if action == "Repair" and random.random() < 0.75:
            for acc in random.sample(accessories, random.randint(1, 3)):
                sp = SparePartUsed.objects.create(
                    job_card=jc,
                    part=acc,
                    quantity=random.randint(1, 4),
                    unit_cost=acc.unit_cost,
                    remarks="Drawn from workshop store",
                )
                man["spare_parts"].append(str(sp.id))
                parts_rows += 1
            jc.total_parts_cost = jc.calculate_total_parts_cost()
            jc.save(update_fields=["total_parts_cost", "updated_at"])

        # date_issued is auto_now_add, so backdate it after the insert
        jobcard.objects.filter(pk=jc.pk).update(date_issued=issued)

    print(f"  created {sum(made.values())} job cards "
          f"({made['Approved']} approved, {made['Waiting Approval']} waiting, "
          f"{made['Declined']} declined)")
    print(f"  created {parts_rows} spare-part lines")

    # ---- PPM history ----
    print("PPM history")
    past = list(
        PPMSchedule.objects.filter(
            scheduled_month__lt=today.replace(day=1), status="pending"
        )[:140]
    )
    completed_n = 0
    for sched in past:
        if random.random() < 0.75:
            man["ppm_reverts"][str(sched.id)] = sched.status
            PPMSchedule.objects.filter(pk=sched.pk).update(status="completed")
            completed_n += 1
    print(f"  marked {completed_n} past schedules completed")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(man, indent=2))
    print(f"\nManifest: {MANIFEST}")
    print("Undo with: python helper_scripts/seed_demo_data.py --undo")


def seed_ppm_history():
    """Add completed PPM history behind the existing forward schedule.

    A fresh instance generates its schedules all in one upcoming month, which
    leaves the PPM module and the completions chart with no past to show. Each
    device keeps its upcoming schedule; this adds the months before it.
    """
    random.seed(SEED + 1)
    man = load_manifest()
    man.setdefault("ppm_created", [])

    equipment = list(
        Equipment.objects.select_related("department", "workshop").filter(active_status=True)
    )
    today = date.today()
    first_of_month = today.replace(day=1)

    made, completed_n = 0, 0
    for eq in equipment:
        if not (eq.department and eq.department.workshop):
            continue
        # a device is serviced every 3, 4 or 6 months — walk that interval back
        interval = random.choice([3, 4, 6])
        for step in range(1, random.randint(2, 4)):
            months_back = interval * step
            m = first_of_month.month - months_back
            y = first_of_month.year
            while m <= 0:
                m += 12
                y -= 1
            month = date(y, m, 1)

            if PPMSchedule.objects.filter(equipment=eq, scheduled_month=month).exists():
                continue

            status = "completed" if random.random() < 0.85 else "pushed"
            sched = PPMSchedule(
                equipment=eq,
                workshop=eq.department.workshop,
                scheduled_month=month,
                status=status,
                planning_logic="description_based",
                maintenance_period=interval,
                generation_source="bulk_import",
            )
            sched.save()
            man["ppm_created"].append(str(sched.id))
            made += 1
            if status == "completed":
                completed_n += 1

    MANIFEST.write_text(json.dumps(man, indent=2))
    print(f"  created {made} past schedules ({completed_n} completed)")
    print(f"  manifest updated: {MANIFEST}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--undo", action="store_true", help="remove everything this script created")
    ap.add_argument("--ppm-only", action="store_true", help="only add past PPM history")
    args = ap.parse_args()
    if args.undo:
        undo()
    elif args.ppm_only:
        seed_ppm_history()
    else:
        seed()
        print("PPM history")
        seed_ppm_history()
