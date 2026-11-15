import argparse

from app import create_app
from app.reminder_scheduler import (
    list_test_reminder_targets,
    run_forever,
    send_test_reminder,
)


def _arguments():
    parser = argparse.ArgumentParser(description="Run VaxiCare vaccination reminders.")
    parser.add_argument(
        "--list-test-reminder-targets",
        action="store_true",
        help="List incomplete doses for the parent matching REMINDER_TEST_EMAIL.",
    )
    parser.add_argument("--test-child-id", type=int)
    parser.add_argument("--test-schedule-id", type=int)
    parser.add_argument(
        "--test-reminder-type",
        choices=("upcoming", "due_today", "overdue"),
    )
    args = parser.parse_args()
    test_arguments = (
        args.test_child_id is not None
        or args.test_schedule_id is not None
        or args.test_reminder_type is not None
    )
    if args.list_test_reminder_targets and test_arguments:
        parser.error("Use --list-test-reminder-targets separately from send options.")
    if test_arguments and (
        args.test_child_id is None or args.test_reminder_type is None
    ):
        parser.error("--test-child-id and --test-reminder-type must be provided together.")
    if args.test_child_id is not None and args.test_schedule_id is None:
        parser.error("--test-schedule-id is required to select the dose to test.")
    return parser, args


if __name__ == "__main__":
    parser, args = _arguments()
    if args.list_test_reminder_targets or args.test_child_id is not None:
        application = create_app({"SEED_DEMO_DATA": False})
        if args.list_test_reminder_targets:
            try:
                targets = list_test_reminder_targets(application)
            except ValueError as exc:
                parser.error(str(exc))
            if not targets:
                print("No incomplete doses found for the configured reminder test parent.")
            for target in targets:
                print(
                    "child_id={child_id} schedule_id={schedule_id} "
                    "child={child_name} vaccine={vaccine_name} dose={dose_label} "
                    "due={due_date}".format(**target)
                )
        else:
            try:
                result = send_test_reminder(
                    application,
                    child_id=args.test_child_id,
                    schedule_id=args.test_schedule_id,
                    reminder_type=args.test_reminder_type,
                )
            except ValueError as exc:
                parser.error(str(exc))
            print("Reminder test status: {}.".format(result["status"]))
            print(result["message"])
            if "smtp_response" in result:
                print("SMTP response: {}".format(result["smtp_response"]))
            if result["status"] not in {"sent", "duplicate"}:
                raise SystemExit(1)
    else:
        application = create_app()
        run_forever(application)
