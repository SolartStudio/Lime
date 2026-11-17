"""Запуск отдельно (для проверки):  EXPENSES_PASSWORD=... python -m expenses"""
from flask import Flask
from . import bp
app = Flask(__name__)
app.register_blueprint(bp)
if __name__ == "__main__":
    app.run("127.0.0.1", 8099)
