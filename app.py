from flask import Flask
import os

from db import init_db, seed_sample_data
from routes import register_routes

app = Flask(__name__)
register_routes(app)


init_db()
seed_sample_data()


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1" )