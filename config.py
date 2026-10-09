import os

# BotFather se mila token yahan ya env var me daalo
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")

# Har turn ka time (seconds)
TURN_SECONDS = int(os.environ.get("TURN_SECONDS", "35"))

# Kam se kam kitne players chahiye game start ke liye
MIN_PLAYERS = int(os.environ.get("MIN_PLAYERS", "1"))

# Flask status server
ENABLE_FLASK = os.environ.get("ENABLE_FLASK", "1") == "1"
PORT = int(os.environ.get("PORT", "5000"))
