from flask import Flask
import os

from db import init_db
from routes import register_routes

app = Flask(__name__)
register_routes(app)

if __name__ == "__main__":
    init_db()
    app.run(debug=os.getenv("FLASK_DEBUG") == "1" )