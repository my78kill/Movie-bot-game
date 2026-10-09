from flask import Flask, jsonify

import config


def create_app(manager):
    app = Flask(__name__)

    @app.route("/")
    def index():
        return "Movie Chain Bot is running ✅"

    @app.route("/health")
    def health():
        return jsonify(status="ok", games=len(manager.games))

    @app.route("/games")
    def games():
        data = {}
        for cid, g in manager.games.items():
            data[str(cid)] = {
                "genre": g.genre,
                "started": g.started,
                "players": [p["name"] for p in g.players.values()],
                "scores": {p["name"]: p["score"] for p in g.players.values()},
            }
        return jsonify(data)

    return app


def run_flask(manager):
    app = create_app(manager)
    app.run(host="0.0.0.0", port=config.PORT)
