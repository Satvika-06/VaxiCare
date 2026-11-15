from app import create_app, db
from app.seed_data import seed_demo_data

app = create_app()

with app.app_context():
    db.create_all()
    if app.config["SEED_DEMO_DATA"]:
        seed_demo_data()
        print("Database initialized with demo records.")
    else:
        print("Database initialized. Demo seeding is disabled.")
