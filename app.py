from flask import Flask

from db import init_db
from routes import register_routes

app = Flask(__name__)
register_routes(app)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)