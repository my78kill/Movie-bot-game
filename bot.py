import html
import random
import threading
import time

import requests

import config

API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
TURN_SECONDS = config.TURN_SECONDS


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

WELCOME = (
    "🎬 <b>Welcome to Movie Chain Bot!</b>\n\n"
    "Yahan Bollywood aur Hollywood movie chain game khel sakte ho.\n\n"
    "<b>Kaise khelein:</b>\n"
    "1️⃣ /game — genre choose karo (Bollywood / Hollywood)\n"
    "2️⃣ /join — game join karo\n"
    "3️⃣ Host /startgame se game shuru karega\n"
    "4️⃣ Bot jo letter dega, us letter se movie ka naam bhejo\n"
    "5️⃣ ⏳ 35 sec per turn, repeat naam allowed nahi\n\n"
    "<b>Commands:</b>\n"
    "/game — naya game banao\n"
    "/join — lobby join karo\n"
    "/startgame — host game shuru kare\n"
    "/leave — game se nikal jao\n"
    "/stop — host game band kare\n"
    "/result — scoreboard dekho"
)


class Game:
    def __init__(self, chat_id, genre, host_id, host_name):
        self.chat_id = chat_id
        self.genre = genre
        self.host_id = host_id
        self.players = {host_id: {"name": host_name, "score": 0}}
        self.order = [host_id]
        self.started = False
        self.used = set()
        self.letter = None
        self.turn_index = 0
        self.timer = None
        self.lobby_msg_id = None
        self.lock = threading.RLock()

    # ---------- helpers ----------
    def movie_set(self):
        return BOLLYWOOD if self.genre == "bollywood" else HOLLYWOOD

    def _valid_starts(self):
        return sorted({m[0] for m in self.movie_set()})

    def _last_letter(self, movie):
        for ch in reversed(movie):
            if ch.isalpha():
                return ch
        return random.choice(self._valid_starts())

    def send(self, text, keyboard=None):
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        res = api("sendMessage", **payload)
        return res.get("result", {}).get("message_id")

    def lobby_keyboard(self):
        return [
            [{"text": "➕ Join (/join)", "callback_data": "join"},
             {"text": "🚪 Leave (/leave)", "callback_data": "leave"}],
            [{"text": "▶️ Start (/startgame)", "callback_data": "startgame"},
             {"text": "⛔ Stop (/stop)", "callback_data": "stop"}],
        ]

    # ---------- lobby ----------
    def show_lobby(self):
        names = "\n".join(
            f"{i + 1}. {html.escape(p['name'])}" for i, p in enumerate(self.players.values())
        )
        text = (
            f"🎬 <b>Movie Chain — {self.genre.title()}</b>\n\n"
            f"👑 Host: {html.escape(self.players[self.host_id]['name'])}\n\n"
            f"👥 <b>Joined Players ({len(self.players)}):</b>\n{names}\n\n"
            f"Join karne ke liye <b>/join</b> bhejo.\n"
            f"Host <b>/startgame</b> bhejega to game shuru hoga.\n"
            f"<i>Rules: bot jo letter dega us letter se movie banao, "
            f"{TURN_SECONDS} sec per turn, repeat naam allowed nahi.</i>"
        )
        if self.lobby_msg_id:
            api("editMessageText", chat_id=self.chat_id, message_id=self.lobby_msg_id,
                text=text, parse_mode="HTML",
                reply_markup={"inline_keyboard": self.lobby_keyboard()})
        else:
            self.lobby_msg_id = self.send(text, self.lobby_keyboard())

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
            self.send(f"Kam se kam {config.MIN_PLAYERS} player chahiye.")
            return
        self.started = True
        self.order = list(self.players.keys())
        random.shuffle(self.order)
        self.turn_index = 0
        self.letter = random.choice(self._valid_starts())
        if self.lobby_msg_id:
            api("editMessageText", chat_id=self.chat_id, message_id=self.lobby_msg_id,
                text=f"🎬 <b>Game started!</b> Genre: {self.genre.title()}\n"
                     f"End karne ke liye host /stop bheje.",
                parse_mode="HTML")
        self.begin_turn()

    def _clear_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def begin_turn(self):
        with self.lock:
            self._clear_timer()
            uid = self.order[self.turn_index]
            name = self.players[uid]["name"]
            self.send(
                f"🎯 <b>{html.escape(name)}</b> ki turn!\n\n"
                f"Letter: <b>{self.letter.upper()}</b>\n"
                f"⏳ {TURN_SECONDS} sec me movie ka naam bhejo."
            )
            self.timer = threading.Timer(TURN_SECONDS, self.timeout)
            self.timer.daemon = True
            self.timer.start()

    def _advance(self):
        self.turn_index = (self.turn_index + 1) % len(self.order)

    def submit(self, uid, text):
        if not self.started or not self.order:
            return False
        with self.lock:
            if uid != self.order[self.turn_index]:
                return False
            movie = text.strip().lower()
            if not movie:
                return False
            name = html.escape(self.players[uid]["name"])

            if not movie.startswith(self.letter):
                return self._wrong(
                    f"❌ '<b>{html.escape(text)}</b>' letter "
                    f"<b>{self.letter.upper()}</b> se shuru nahi hota."
                )
            if movie not in self.movie_set():
                return self._wrong(
                    f"❌ '<b>{html.escape(text)}</b>' list me nahi hai. Valid movie name do."
                )
            if movie in self.used:
                return self._wrong(
                    f"❌ '<b>{html.escape(text)}</b>' pehle use ho chuki hai."
                )

            self._clear_timer()
            self.used.add(movie)
            self.players[uid]["score"] += 1
            self.send(f"✅ <b>{html.escape(text)}</b> sahi! +1 point → {name}")

            self.letter = self._last_letter(movie)
            if not any(m.startswith(self.letter) for m in self.movie_set()):
                self.letter = random.choice(self._valid_starts())
            self._advance()
            self.begin_turn()
            return True

    def _wrong(self, reason):
        self._clear_timer()
        self.send(reason)
        self._advance()
        self.begin_turn()
        return True

    def timeout(self):
        with self.lock:
            if not self.started or not self.order:
                return
            uid = self.order[self.turn_index]
            name = html.escape(self.players[uid]["name"])
            self.send(f"⏰ Time out! {name} ne jawab nahi diya.")
            self._advance()
            self.begin_turn()

    def stop(self, by_name):
        self._clear_timer()
        self.started = False
        self.send(f"⛔ Game stopped by {html.escape(by_name)}.")
        self.show_result()

    def show_result(self):
        ranked = sorted(self.players.items(), key=lambda x: -x[1]["score"])
        lines = ["🏁 <b>RESULT BOARD</b> 🏁", ""]
        medals = ["👑", "🥈", "🥉"]
        for i, (uid, p) in enumerate(ranked):
            tag = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{tag} {html.escape(p['name'])} — {p['score']} pts")
        if ranked:
            lines.append("")
            lines.append(f"🎉 Winner: <b>{html.escape(ranked[0][1]['name'])}</b> 👑")
        self.send("\n".join(lines))


class GameManager:
    def __init__(self):
        self.games = {}

    def handle_update(self, update):
        if "message" in update:
            self.on_message(update["message"])
        elif "callback_query" in update:
            self.on_callback(update["callback_query"])

    # ---------- messages ----------
    def on_message(self, msg):
        chat = msg["chat"]
        chat_id = chat["id"]
        text = msg.get("text", "")
        user = msg.get("from", {})
        uid = user.get("id")
        name = user.get("first_name", "Player")
        is_private = chat.get("type") == "private"

        if text.startswith("/start") or text.startswith("/help"):
            self.cmd_welcome(chat_id, name)
        elif text.startswith("/game"):
            self.cmd_game(chat_id, uid, name)
        elif text.startswith("/join"):
            self.do_join(chat_id, uid, name)
        elif text.startswith("/startgame"):
            self.do_startgame(chat_id, uid)
        elif text.startswith("/stop"):
            g = self.games.get(chat_id)
            if g and uid == g.host_id:
                g.stop(name)
                self.games.pop(chat_id, None)
            elif g:
                api("sendMessage", chat_id=chat_id,
                    text="Sirf host game stop kar sakta hai.")
            else:
                api("sendMessage", chat_id=chat_id, text="Koi game nahi chal raha.")
        elif text.startswith("/leave"):
            self.do_leave(chat_id, uid)
        elif text.startswith("/result"):
            g = self.games.get(chat_id)
            if g:
                g.show_result()
            else:
                api("sendMessage", chat_id=chat_id, text="Koi game nahi chal raha.")
        else:
            g = self.games.get(chat_id)
            if g and g.started and text and not text.startswith("/"):
                g.submit(uid, text)

    def cmd_welcome(self, chat_id, name):
        api("sendMessage", chat_id=chat_id, text=WELCOME, parse_mode="HTML")

    def cmd_game(self, chat_id, uid, name):
        g = self.games.get(chat_id)
        if g and g.started:
            api("sendMessage", chat_id=chat_id,
                text="⚠️ Ek game already chal raha hai. /stop karke naya shuru karo.")
            return
        keyboard = [[
            {"text": "🎬 Bollywood", "callback_data": "genre:bollywood"},
            {"text": "🎥 Hollywood", "callback_data": "genre:hollywood"},
        ]]
        api("sendMessage", chat_id=chat_id,
            text=f"🎬 <b>Movie Chain Game</b>\n\n{html.escape(name)}, genre choose karo:",
            parse_mode="HTML",
            reply_markup={"inline_keyboard": keyboard})

    def do_join(self, chat_id, uid, name):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id,
                text="Pehle /game se genre choose karo.")
            return
        if g.started:
            api("sendMessage", chat_id=chat_id,
                text="Game already start ho gaya, ab join nahi ho sakte.")
            return
        if uid in g.players:
            api("sendMessage", chat_id=chat_id, text="Tum already joined ho.")
            return
        g.players[uid] = {"name": name, "score": 0}
        api("sendMessage", chat_id=chat_id, text=f"✅ {html.escape(name)} join ho gaya!")
        g.show_lobby()

    def do_startgame(self, chat_id, uid):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="Pehle /game se shuru karo.")
            return
        if uid != g.host_id:
            api("sendMessage", chat_id=chat_id,
                text="Sirf host /startgame kar sakta hai.")
            return
        if g.started:
            api("sendMessage", chat_id=chat_id, text="Game already start ho chuka hai.")
            return
        g.start_game()

    def do_leave(self, chat_id, uid):
        g = self.games.get(chat_id)
        if not g:
            api("sendMessage", chat_id=chat_id, text="Koi game nahi chal raha.")
            return
        if uid not in g.players:
            api("sendMessage", chat_id=chat_id, text="Tum joined nahi ho.")
            return
        name = g.players[uid]["name"]
        g.remove_player(uid)
        if not g.players:
            g._clear_timer()
            self.games.pop(chat_id, None)
            api("sendMessage", chat_id=chat_id, text="Sab leave kar gaye. Game khatam.")
            return
        api("sendMessage", chat_id=chat_id,
            text=f"🚪 {html.escape(name)} game se leave kar gaya.")
        g.show_lobby()

    # ---------- callbacks (buttons optional) ----------
    def on_callback(self, cq):
        data = cq["data"]
        chat_id = cq["message"]["chat"]["id"]
        uid = cq["from"]["id"]
        name = cq["from"].get("first_name", "Player")
        msg_id = cq["message"]["message_id"]

        def answer(text=""):
            api("answerCallbackQuery", callback_query_id=cq["id"], text=text)

        if data.startswith("genre:"):
            g = self.games.get(chat_id)
            if g and g.started:
                answer("Game already running!")
                return
            genre = data.split(":")[1]
            g = Game(chat_id, genre, uid, name)
            g.lobby_msg_id = msg_id
            self.games[chat_id] = g
            answer(f"{genre.title()} selected")
            g.show_lobby()
            return

        g = self.games.get(chat_id)
        if not g:
            answer("Pehle /game karo")
            return

        if data == "join":
            if g.started:
                answer("Game chal raha hai, wait karo.")
            elif uid in g.players:
                answer("Already joined.")
            else:
                g.players[uid] = {"name": name, "score": 0}
                answer("Joined!")
                g.show_lobby()

        elif data == "leave":
            if uid not in g.players:
                answer("Tum joined nahi ho.")
            else:
                answer("Left")
                self.do_leave(chat_id, uid)

        elif data == "startgame":
            if uid != g.host_id:
                answer("Sirf host start kar sakta hai.")
            elif g.started:
                answer("Already started.")
            else:
                answer("Starting...")
                g.start_game()

        elif data == "stop":
            if uid != g.host_id:
                answer("Sirf host stop kar sakta hai.")
            else:
                answer("Stopped")
                g.stop(name)
                self.games.pop(chat_id, None)


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
