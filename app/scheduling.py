from datetime import date, timedelta

from app.models import VaccinationSchedule


def build_child_schedule(child, today=None):
    today = today or date.today()
    schedules = VaccinationSchedule.query.join(VaccinationSchedule.vaccine).order_by(
        VaccinationSchedule.age_days.asc(), VaccinationSchedule.id.asc()
    ).all()

    completed_ids = {
        record.schedule_id for record in child.records if record.schedule_id is not None
    }
    legacy_by_vaccine = {}
    for record in child.records:
        if record.schedule_id is None:
            legacy_by_vaccine.setdefault(record.vaccine_id, []).append(record)

    schedule_by_vaccine = {}
    for schedule in schedules:
        schedule_by_vaccine.setdefault(schedule.vaccine_id, []).append(schedule)

    for vaccine_id, records in legacy_by_vaccine.items():
        remaining = [
            schedule
            for schedule in schedule_by_vaccine.get(vaccine_id, [])
            if schedule.id not in completed_ids
        ]
        for schedule in remaining[: len(records)]:
            completed_ids.add(schedule.id)

    entries = []
    for schedule in schedules:
        due_date = child.dob + timedelta(days=schedule.age_days)
        if schedule.id in completed_ids:
            status = "completed"
        elif due_date == today:
            status = "due_today"
        elif due_date < today:
            status = "overdue"
        else:
            status = "upcoming"
        entries.append(
            {
                "schedule": schedule,
                "vaccine": schedule.vaccine,
                "due_date": due_date,
                "status": status,
                "label": schedule.dose_label,
            }
        )
    return entries
