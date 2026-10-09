import html
import random
import threading
import time

import requests

import config

# ======================== CONFIG / SETUP ========================
API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
TURN_SECONDS = config.TURN_SECONDS
ALPHABET = list("abcdefghijklmnopqrstuvwxyz")

WELCOME = (
    "🎬 <b>Welcome to Movie Word Chain!</b>\n\n"
    "Play a movie chain with your friends.\n\n"
    "<b>Commands:</b>\n"
    "/join — join the game\n"
    "/leave — leave the game\n"
    "/players — show all players\n"
    "/playgame — start the game (min 2 players)\n"
    "/stopgame — stop the running game\n\n"
    "<b>Rules:</b> The bot gives a letter, the mentioned player sends a movie "
    "starting with it. Last letter of the movie becomes the next letter. "
    "35 seconds per turn, no repeated movies."
)


def api(method, **payload):
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=25)
        return r.json()
    except Exception as e:
        print("API error:", method, e)
        return {}


def load_movies(filename):
    movies = set()
    try:
        with open(f"movies/{filename}", encoding="utf-8") as f:
            for line in f:
                m = line.strip()
                if m:
                    movies.add(m.lower())
    except FileNotFoundError:
        print("Missing file:", filename)
    return movies


# combined movie database (lowercased for case-insensitive matching)
MOVIES = load_movies("bollywood.txt") | load_movies("hollywood.txt")


def mention(uid, player):
    if player.get("username"):
        return f"@{player['username']}"
    return f"<b>{html.escape(player['name'])}</b>"


def last_letter(movie):
    """Last English alphabet letter, ignoring trailing spaces/punctuation."""
    for ch in reversed(movie):
        if ch.isalpha():
            return ch.lower()
    return random.choice(ALPHABET)


# ======================== GAME STATE ========================
class Game:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players = {}       # uid -> {"name", "username", "score"}
        self.order = []         # shuffled uids (active players)
        self.started = False
        self.used = set()       # used movies (lowercased)
        self.letter = None      # required starting letter
        self.turn_index = 0
        self.turn_id = 0        # invalidates stale timers
        self.timer = None
        self.lock = threading.RLock()

    # ---------- io ----------
    def send(self, text):
        return api("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML")

    def result_text(self):
        ranked = sorted(self.players.items(), key=lambda x: -x[1]["score"])
        if not ranked:
            return "🏁 <b>Result Board</b>\nNo players."
        lines = ["🏁 <b>Result Board</b>", ""]
        medals = ["👑", "🥈", "🥉"]
        for i, (uid, p) in enumerate(ranked):
            tag = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{tag} {mention(uid, p)} — {p['score']} pts")
        return "\n".join(lines)

    # ---------- lobby ----------
    def add_player(self, uid, name, username):
        if uid in self.players:
            return False
        self.players[uid] = {"name": name, "username": username, "score": 0}
        return True

    def players_text(self):
        if not self.players:
            return "👥 No players yet. Use /join to participate."
        lines = "\n".join(
            f"{i + 1}. {mention(uid, p)}"
            for i, (uid, p) in enumerate(self.players.items())
        )
        return f"👥 <b>Players ({len(self.players)}):</b>\n{lines}"

    # ---------- start ----------
    def start(self):
        if self.started:
            return "already"
        if len(self.players) < 2:
            return "need2"
        self.started = True
        self.order = list(self.players.keys())
        random.shuffle(self.order)
        self.turn_index = 0
        self.used = set()
        self.letter = random.choice(ALPHABET)
        self.send(
            f"🎬 <b>Game started!</b>\n"
            f"Players: {len(self.order)} | First letter: <b>{self.letter.upper()}</b>\n"
            f"Host can /stopgame anytime."
        )
        self.begin_turn()
        return "ok"

    # ---------- turn ----------
    def _clear_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def current_uid(self):
        return self.order[self.turn_index] if self.order else None

    def begin_turn(self):
        with self.lock:
            self._clear_timer()
            self.turn_id += 1
            tid = self.turn_id
            uid = self.current_uid()
            if uid is None:
                return
            who = mention(uid, self.players[uid])
            self.send(
                f"🎯 {who}, send a movie starting with letter "
                f"<b>{self.letter.upper()}</b>\n⏳ {TURN_SECONDS} seconds."
            )
            self.timer = threading.Timer(TURN_SECONDS, self._on_timeout, args=(tid,))
            self.timer.daemon = True
            self.timer.start()

    def _advance(self):
        if self.order:
            self.turn_index = (self.turn_index + 1) % len(self.order)

    # ---------- answers ----------
    def submit(self, uid, text):
        if not self.started or not self.order:
            return
        with self.lock:
            # only the current player can answer
            if uid != self.current_uid():
                return

            movie = text.strip().lower()
            if not movie:
                return

            # invalid -> explain and let them retry (timer keeps running)
            if not movie.startswith(self.letter):
                self.send(
                    f"❌ '<b>{html.escape(text)}</b>' does not start with "
                    f"<b>{self.letter.upper()}</b>. Try again."
                )
                return
            if movie not in MOVIES:
                self.send(
                    f"❌ '<b>{html.escape(text)}</b>' is not in the movie database. "
                    f"Try again."
                )
                return
            if movie in self.used:
                self.send(
                    f"❌ '<b>{html.escape(text)}</b>' was already used. Try again."
                )
                return

            # valid
            self._clear_timer()
            self.used.add(movie)
            self.players[uid]["score"] += 1
            who = mention(uid, self.players[uid])
            self.send(f"✅ <b>{html.escape(text)}</b> correct! +1 point to {who}")

            self.letter = last_letter(movie)
            self._advance()
            self.begin_turn()

    # ---------- timeout / elimination ----------
    def _on_timeout(self, tid):
        with self.lock:
            if not self.started or tid != self.turn_id:
                return  # stale timer
            uid = self.current_uid()
            if uid is None:
                return
            who = mention(uid, self.players[uid])
            self.send(f"⏰ Time out! {who} is eliminated.")
            self._eliminate(uid)

    def _eliminate(self, uid):
        self.players.pop(uid, None)
        if uid in self.order:
            idx = self.order.index(uid)
            self.order.remove(uid)
            if idx < self.turn_index:
                self.turn_index -= 1
            if self.order:
                self.turn_index %= len(self.order)
        if len(self.players) <= 1:
            self.finish()
        else:
            self.begin_turn()

    def remove_player(self, uid):
        """Player voluntarily leaves (lobby or active game)."""
        with self.lock:
            if uid not in self.players:
                return False
            was_current = self.started and self.current_uid() == uid
            self.players.pop(uid, None)
            if uid in self.order:
                idx = self.order.index(uid)
                self.order.remove(uid)
                if self.started:
                    if idx < self.turn_index:
                        self.turn_index -= 1
                    if self.order:
                        self.turn_index %= len(self.order)
            if self.started:
                if was_current:
                    self._clear_timer()
                    self.turn_id += 1  # invalidate pending timer
                    if len(self.players) <= 1:
                        self.finish()
                    else:
                        self.begin_turn()
            return True

    # ---------- end ----------
    def finish(self):
        with self.lock:
            self._clear_timer()
            self.started = False
            self.turn_id += 1
            if len(self.players) == 1:
                uid = next(iter(self.players))
                self.send(f"🏆 {mention(uid, self.players[uid])} is the WINNER! 👑")
            elif len(self.players) == 0:
                self.send("🏁 No winner — everyone is out.")
            self.send(self.result_text())
            # reset to lobby state
            self.players = {}
            self.order = []
            self.used = set()
            self.letter = None
            self.turn_index = 0

    def stop(self):
        with self.lock:
            self._clear_timer()
            self.started = False
            self.turn_id += 1


# ======================== COMMAND HANDLERS ========================
class GameManager:
    def __init__(self):
        self.games = {}  # chat_id -> Game

    def get_game(self, chat_id):
        return self.games.get(chat_id)

    def handle_update(self, update):
        try:
            if "message" in update:
                self.on_message(update["message"])
        except Exception as e:
            print("update error:", e)

    def on_message(self, msg):
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            return
        user = msg.get("from", {})
        uid = user.get("id")
        name = user.get("first_name", "Player")
        username = user.get("username")

        text = msg.get("text")
        if not text:  # non-text message -> ignore safely
            return
        text = text.strip()
        low = text.lower()

        if text.startswith("/"):
            cmd = low.split()[0].split("@")[0]
        else:
            cmd = None

        if cmd in ("/start", "/help"):
            api("sendMessage", chat_id=chat_id, text=WELCOME, parse_mode="HTML")
        elif cmd == "/join":
            self.cmd_join(chat_id, uid, name, username)
        elif cmd == "/leave":
            self.cmd_leave(chat_id, uid)
        elif cmd == "/players":
            self.cmd_players(chat_id)
        elif cmd == "/playgame":
            self.cmd_playgame(chat_id)
        elif cmd == "/stopgame":
            self.cmd_stopgame(chat_id)
        elif cmd is not None:
            pass  # unknown command -> ignore
        else:
            g = self.get_game(chat_id)
            if g and g.started:
                g.submit(uid, text)

    def cmd_join(self, chat_id, uid, name, username):
        g = self.get_game(chat_id)
        if g is None:
            g = Game(chat_id)
            self.games[chat_id] = g
        if g.started:
            api("sendMessage", chat_id=chat_id,
                text="Game already started, wait for the next round.")
            return
        if g.add_player(uid, name, username):
            api("sendMessage", chat_id=chat_id,
                text=f"✅ {mention(uid, g.players[uid])} joined!")
            g.send(g.players_text())
        else:
            api("sendMessage", chat_id=chat_id, text="You already joined.")

    def cmd_leave(self, chat_id, uid):
        g = self.get_game(chat_id)
        if not g or uid not in g.players:
            api("sendMessage", chat_id=chat_id, text="You are not in the game.")
            return
        name = g.players[uid]["name"]
        g.remove_player(uid)
        api("sendMessage", chat_id=chat_id,
            text=f"🚪 <b>{html.escape(name)}</b> left the game.", parse_mode="HTML")
        if not g.started:
            g.send(g.players_text())

    def cmd_players(self, chat_id):
        g = self.get_game(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id,
                text="👥 No players yet. Use /join to participate.")
            return
        g.send(g.players_text())

    def cmd_playgame(self, chat_id):
        g = self.get_game(chat_id)
        if not g or len(g.players) < 2:
            api("sendMessage", chat_id=chat_id,
                text="Please join to start the game! Minimum players required: 2. "
                     "Use /join to participate.")
            return
        res = g.start()
        if res == "already":
            api("sendMessage", chat_id=chat_id, text="Game is already running.")
        elif res == "need2":
            api("sendMessage", chat_id=chat_id,
                text="Please join to start the game! Minimum players required: 2. "
                     "Use /join to participate.")

    def cmd_stopgame(self, chat_id):
        g = self.get_game(chat_id)
        if not g or not g.started:
            api("sendMessage", chat_id=chat_id, text="No game is running.")
            return
        g.stop()
        api("sendMessage", chat_id=chat_id, text="⛔ Game stopped. State cleared.")
        self.games.pop(chat_id, None)


manager = GameManager()


# ======================== POLLING (unchanged) ========================
def run_bot():
    api("deleteWebhook", drop_pending_updates=False)
    offset = None
    print("Bot polling started...")
    while True:
        try:
            res = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            ).json()
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                manager.handle_update(u)
        except Exception as e:
            print("poll error:", e)
            time.sleep(2)


if __name__ == "__main__":
    if config.ENABLE_FLASK:
        from live import run_flask
        threading.Thread(target=run_flask, args=(manager,), daemon=True).start()
    run_bot()
