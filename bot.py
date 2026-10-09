import html
import random
import threading
import time

import requests

import config

API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
TURN_SECONDS = config.TURN_SECONDS

WELCOME = (
    "🎬 <b>Welcome to Movie Chain Bot!</b>\n\n"
    "Play Bollywood or Hollywood movie chain with your friends.\n\n"
    "<b>How to play:</b>\n"
    "1. /game — choose a genre\n"
    "2. /join — join the lobby\n"
    "3. Host sends /startgame to begin\n"
    "4. The bot gives a letter, the mentioned player sends a movie\n"
    "5. 35 seconds per turn, no repeated movies\n\n"
    "<b>Commands:</b>\n"
    "/game — create a new game\n"
    "/join — join the lobby\n"
    "/startgame — host starts the game\n"
    "/leave — leave the game\n"
    "/stop — host stops the game\n"
    "/result — show the scoreboard"
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


BOLLYWOOD = load_movies("bollywood.txt")
HOLLYWOOD = load_movies("hollywood.txt")

GENRES = {"bollywood": "Bollywood", "hollywood": "Hollywood"}


def mention(uid, player):
    """Return a clickable mention for a player."""
    if player.get("username"):
        return f"@{player['username']}"
    return f"<b>{html.escape(player['name'])}</b>"


class Game:
    def __init__(self, chat_id, genre, host_id, host_name, host_username):
        self.chat_id = chat_id
        self.genre = genre
        self.host_id = host_id
        self.players = {
            host_id: {"name": host_name, "username": host_username, "score": 0}
        }
        self.order = [host_id]
        self.started = False
        self.used = set()
        self.letter = None
        self.turn_index = 0
        self.turn_id = 0
        self.timer = None
        self.lock = threading.RLock()

    # ---------- helpers ----------
    def movie_set(self):
        return BOLLYWOOD if self.genre == "bollywood" else HOLLYWOOD

    def pick_letter(self):
        """Random letter that still has at least one unused movie."""
        available = [
            c for c in "abcdefghijklmnopqrstuvwxyz"
            if any(m.startswith(c) and m not in self.used for m in self.movie_set())
        ]
        return random.choice(available) if available else random.choice("abcdefghijklmnopqrstuvwxyz")

    def send(self, text):
        res = api("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML")
        return res.get("result", {}).get("message_id")

    # ---------- lobby ----------
    def show_lobby(self):
        lines = "\n".join(
            f"{i + 1}. {mention(uid, p)}"
            for i, (uid, p) in enumerate(self.players.items())
        )
        text = (
            f"🎬 <b>Movie Chain — {GENRES[self.genre]}</b>\n\n"
            f"👑 Host: {mention(self.host_id, self.players[self.host_id])}\n\n"
            f"👥 <b>Joined Players ({len(self.players)}):</b>\n{lines}\n\n"
            f"Send <b>/join</b> to enter.\n"
            f"Host sends <b>/startgame</b> to begin."
        )
        self.send(text)

    def remove_player(self, uid):
        self.players.pop(uid, None)
        if uid in self.order:
            idx = self.order.index(uid)
            self.order.remove(uid)
            if self.started and self.order:
                if idx < self.turn_index:
                    self.turn_index -= 1
                self.turn_index %= len(self.order)
        if uid == self.host_id and self.players:
            self.host_id = next(iter(self.players))

    # ---------- game ----------
    def start_game(self):
        if self.started:
            return
        if len(self.players) < config.MIN_PLAYERS:
            self.send(f"Need at least {config.MIN_PLAYERS} player(s) to start.")
            return
        self.started = True
        self.order = list(self.players.keys())
        random.shuffle(self.order)
        self.turn_index = 0
        self.send(f"🎬 <b>Game started!</b> Genre: {GENRES[self.genre]}\n"
                  f"Host sends /stop to end the game.")
        self.begin_turn()

    def _clear_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def current_uid(self):
        return self.order[self.turn_index]

    def begin_turn(self):
        with self.lock:
            self._clear_timer()
            self.turn_id += 1
            tid = self.turn_id
            uid = self.current_uid()
            self.letter = self.pick_letter()
            who = mention(uid, self.players[uid])
            self.send(
                f"🎯 {who}, send a movie name starting with letter "
                f"<b>{self.letter.upper()}</b>\n"
                f"⏳ {TURN_SECONDS} seconds."
            )
            self.timer = threading.Timer(TURN_SECONDS, self._on_timeout, args=(tid,))
            self.timer.daemon = True
            self.timer.start()

    def _advance(self):
        self.turn_index = (self.turn_index + 1) % len(self.order)

    def submit(self, uid, text):
        """Only the current player's message is handled. Others are ignored."""
        if not self.started or not self.order:
            return False
        with self.lock:
            # ignore anyone who is not the current player
            if uid != self.current_uid():
                return False

            movie = text.strip().lower()
            if not movie:
                return False

            # --- current player sent something: validate ---
            if not movie.startswith(self.letter):
                self.send(
                    f"❌ '<b>{html.escape(text)}</b>' does not start with "
                    f"<b>{self.letter.upper()}</b>."
                )
                self._advance()
                self.begin_turn()
                return True

            if movie not in self.movie_set():
                self.send(
                    f"❌ '<b>{html.escape(text)}</b>' is not in the movie list."
                )
                self._advance()
                self.begin_turn()
                return True

            if movie in self.used:
                self.send(
                    f"❌ '<b>{html.escape(text)}</b>' was already used."
                )
                self._advance()
                self.begin_turn()
                return True

            # --- correct ---
            self._clear_timer()
            self.used.add(movie)
            self.players[uid]["score"] += 1
            who = mention(uid, self.players[uid])
            self.send(f"✅ <b>{html.escape(text)}</b> correct! +1 point to {who}")
            self._advance()
            self.begin_turn()
            return True

    def _on_timeout(self, tid):
        """Fired by the turn timer. Ignores stale timers from older turns."""
        with self.lock:
            if not self.started or not self.order or tid != self.turn_id:
                return
            uid = self.current_uid()
            who = mention(uid, self.players[uid])
            self.send(f"⏰ Time out! {who} did not answer.")
            self._advance()
            self.begin_turn()

    def stop(self, by_name):
        self._clear_timer()
        self.started = False
        self.turn_id += 1  # invalidate any pending timer
        self.send(f"⛔ Game stopped by <b>{html.escape(by_name)}</b>.")
        self.show_result()

    def show_result(self):
        ranked = sorted(self.players.items(), key=lambda x: -x[1]["score"])
        lines = ["🏁 <b>RESULT BOARD</b> 🏁", ""]
        medals = ["👑", "🥈", "🥉"]
        for i, (uid, p) in enumerate(ranked):
            tag = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{tag} {mention(uid, p)} — {p['score']} pts")
        if ranked:
            lines.append("")
            lines.append(f"🎉 Winner: {mention(ranked[0][0], ranked[0][1])} 👑")
        self.send("\n".join(lines))


class GameManager:
    def __init__(self):
        self.games = {}
        self.pending_genre = {}  # chat_id -> user_id waiting to pick a genre

    def handle_update(self, update):
        if "message" in update:
            self.on_message(update["message"])

    # ---------- messages ----------
    def on_message(self, msg):
        chat = msg["chat"]
        chat_id = chat["id"]
        text = msg.get("text", "").strip()
        user = msg.get("from", {})
        uid = user.get("id")
        name = user.get("first_name", "Player")
        username = user.get("username")

        low = text.lower()

        # parse command: "/startgame@BotName" -> "/startgame"
        if text.startswith("/"):
            cmd = low.split()[0].split("@")[0]
        else:
            cmd = None

        if cmd in ("/start", "/help"):
            self.cmd_welcome(chat_id)
        elif cmd == "/game":
            self.cmd_game(chat_id, uid, name, username, low)
        elif cmd == "/join":
            self.do_join(chat_id, uid, name, username)
        elif cmd == "/startgame":
            self.do_startgame(chat_id, uid)
        elif cmd == "/stop":
            self.do_stop(chat_id, uid, name)
        elif cmd == "/leave":
            self.do_leave(chat_id, uid)
        elif cmd == "/result":
            self.do_result(chat_id)
        else:
            # only for genre picking or a movie answer (game running)
            if self.handle_genre_choice(chat_id, uid, name, username, low):
                return
            g = self.games.get(chat_id)
            if g and g.started and text:
                g.submit(uid, text)

    def cmd_welcome(self, chat_id):
        api("sendMessage", chat_id=chat_id, text=WELCOME, parse_mode="HTML")

    def cmd_game(self, chat_id, uid, name, username, low):
        g = self.games.get(chat_id)
        if g and g.started:
            api("sendMessage", chat_id=chat_id,
                text="⚠️ A game is already running. Send /stop to end it first.")
            return
        parts = low.split()
        if len(parts) > 1:
            arg = parts[1].split("@")[0]
            if arg in GENRES:
                self.create_lobby(chat_id, uid, name, username, arg)
                return
        self.pending_genre[chat_id] = uid
        api("sendMessage", chat_id=chat_id,
            text="🎬 Which genre? Type <b>bollywood</b> or <b>hollywood</b>.",
            parse_mode="HTML")

    def handle_genre_choice(self, chat_id, uid, name, username, low):
        if self.pending_genre.get(chat_id) != uid:
            return False
        if low not in GENRES:
            return False
        self.pending_genre.pop(chat_id, None)
        self.create_lobby(chat_id, uid, name, username, low)
        return True

    def create_lobby(self, chat_id, uid, name, username, genre):
        g = self.games.get(chat_id)
        if g and g.started:
            api("sendMessage", chat_id=chat_id,
                text="⚠️ A game is already running. Send /stop first.")
            return
        g = Game(chat_id, genre, uid, name, username)
        self.games[chat_id] = g
        g.show_lobby()

    def do_join(self, chat_id, uid, name, username):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="Start a game first with /game.")
            return
        if g.started:
            api("sendMessage", chat_id=chat_id,
                text="Game already started, you can't join now.")
            return
        if uid in g.players:
            api("sendMessage", chat_id=chat_id, text="You already joined.")
            return
        g.players[uid] = {"name": name, "username": username, "score": 0}
        g.show_lobby()

    def do_startgame(self, chat_id, uid):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="Start a game first with /game.")
            return
        if uid != g.host_id:
            api("sendMessage", chat_id=chat_id, text="Only the host can start the game.")
            return
        if g.started:
            api("sendMessage", chat_id=chat_id, text="Game already started.")
            return
        g.start_game()

    def do_stop(self, chat_id, uid, name):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="No game is running.")
            return
        if uid != g.host_id:
            api("sendMessage", chat_id=chat_id, text="Only the host can stop the game.")
            return
        g.stop(name)
        self.games.pop(chat_id, None)

    def do_leave(self, chat_id, uid):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="No game is running.")
            return
        if uid not in g.players:
            api("sendMessage", chat_id=chat_id, text="You are not in the game.")
            return
        name = g.players[uid]["name"]
        g.remove_player(uid)
        if not g.players:
            g._clear_timer()
            self.games.pop(chat_id, None)
            api("sendMessage", chat_id=chat_id, text="Everyone left. Game over.")
            return
        api("sendMessage", chat_id=chat_id,
            text=f"🚪 <b>{html.escape(name)}</b> left the game.", parse_mode="HTML")
        g.show_lobby()

    def do_result(self, chat_id):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="No game is running.")
            return
        g.show_result()


manager = GameManager()


# ---------- polling ----------
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
                try:
                    manager.handle_update(u)
                except Exception as e:
                    print("update error:", e)
        except Exception as e:
            print("poll error:", e)
            time.sleep(2)


if __name__ == "__main__":
    if config.ENABLE_FLASK:
        from live import run_flask
        threading.Thread(target=run_flask, args=(manager,), daemon=True).start()
    run_bot()
