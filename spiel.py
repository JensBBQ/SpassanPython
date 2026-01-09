import tkinter as tk
import random
import time
import json
import os
import sys

# -------------------------
# AUSWEICHEN — Arcade Edition (Tkinter)
# Gegner: BBQ-Logo (PNG)
# Spieler: Krankenschein (PNG) — automatisch maximal halb so groß
# -------------------------

WIDTH, HEIGHT = 720, 480
FPS = 60
FRAME_MS = int(1000 / FPS)

BG = "#0D1020"
FG = "#E7EAF0"
ACCENT = "#7EE2B8"
ACCENT2 = "#7AA2FF"
GRID = "#141A2E"
HUD_DIM = "#B9C0D6"

BEST_FILE = "meteor_ausweichen_best.json"


def resource_path(rel_path: str) -> str:
    """
    Funktioniert sowohl im Editor als auch später in einer PyInstaller-EXE.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


LOGO_FILE = resource_path("bbq_logo.png")
PLAYER_FILE = resource_path("krankenschein.png")

# Spieler
PLAYER_Y = HEIGHT - 125         # vertikale Position (oben am Bild)
PLAYER_TARGET_W = 160           # grobe Zielbreite VOR der Halbierung
PLAYER_EXTRA_HALVE = True       # zusätzlich halbieren (50%)

# Logo-Gegner Größenwunsch (wir wählen vorbereitete Varianten)
LOGO_MIN, LOGO_MAX = 24, 64

# Gameplay
GRAZE_MARGIN = 18               # "knapp vorbei"-Zone um den Spieler
GRAZE_BONUS = 12.0
GRAZE_MULT_GAIN = 0.22
MULT_DECAY = 0.35               # Multiplikator fällt pro Sekunde Richtung 1.0

# Bewegung
PLAYER_MAX_SPEED = 520.0
PLAYER_ACCEL = 2400.0
PLAYER_FRICTION = 3200.0

# Sprint
DASH_CD = 1.25                  # Cooldown
DASH_TIME = 0.10                # Dauer
DASH_SPEED = 1150.0

# Gegner
SPAWN_RATE_START = 0.95
SPAWN_ACCEL = 0.065
ENEMY_BASE_SPEED = 190.0
ENEMY_SPEED_ACCEL = 12.0


def clamp(x, a, b):
    return max(a, min(b, x))


def aabb_intersect(a, b) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 < bx1 or ax1 > bx2 or ay2 < by1 or ay1 > by2)


class Game:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Ausweichen — ←/→ bewegen | LEERTASTE Sprint | P Pause | R Neustart | ESC Beenden")
        root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=BG, highlightthickness=0)
        self.canvas.pack()

        # Tastenzustand
        self.left = False
        self.right = False

        root.bind("<KeyPress-Left>", lambda e: self._set_dir("L", True))
        root.bind("<KeyRelease-Left>", lambda e: self._set_dir("L", False))
        root.bind("<KeyPress-Right>", lambda e: self._set_dir("R", True))
        root.bind("<KeyRelease-Right>", lambda e: self._set_dir("R", False))

        # optional: A/D
        root.bind("<KeyPress-a>", lambda e: self._set_dir("L", True))
        root.bind("<KeyRelease-a>", lambda e: self._set_dir("L", False))
        root.bind("<KeyPress-d>", lambda e: self._set_dir("R", True))
        root.bind("<KeyRelease-d>", lambda e: self._set_dir("R", False))

        root.bind("<Escape>", lambda e: root.destroy())
        root.bind("<KeyPress-space>", lambda e: self._dash())
        root.bind("<KeyPress-p>", lambda e: self._toggle_pause())
        root.bind("<KeyPress-P>", lambda e: self._toggle_pause())
        root.bind("<KeyPress-r>", lambda e: self._restart())
        root.bind("<KeyPress-R>", lambda e: self._restart())
        root.bind("<KeyPress-Return>", lambda e: self._start_from_menu())

        self._load_best()
        self._load_logo_varianten()
        self._load_player_image()

        self._init_scene()
        self._to_menu()

        self.last_t = time.perf_counter()
        self._tick()

    # -------------------------
    # Bestwert speichern/laden
    # -------------------------

    def _load_best(self):
        self.best = 0.0
        try:
            if os.path.exists(BEST_FILE):
                with open(BEST_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.best = float(data.get("best", 0.0))
        except Exception:
            self.best = 0.0

    def _save_best(self):
        try:
            with open(BEST_FILE, "w", encoding="utf-8") as f:
                json.dump({"best": round(self.best, 3)}, f)
        except Exception:
            pass

    # -------------------------
    # Bilder laden
    # -------------------------

    def _load_logo_varianten(self):
        """
        Logo mehrfach skaliert vorbereiten (ohne PIL nur subsample/zoom in Ganzzahlen).
        Wichtig: Referenzen halten, sonst verschwinden Bilder.
        """
        self.logo_ok = False
        self.logo_base = None
        self.logo_variants = {}
        self.logo_sizes = [24, 28, 32, 36, 40, 48, 56, 64]

        try:
            self.logo_base = tk.PhotoImage(file=LOGO_FILE)
            bw = self.logo_base.width()

            for target in self.logo_sizes:
                if bw >= target:
                    f = max(1, round(bw / target))
                    img = self.logo_base.subsample(f, f)
                else:
                    f = max(1, round(target / bw))
                    img = self.logo_base.zoom(f, f)

                self.logo_variants[target] = img

            self.logo_ok = True
        except Exception as e:
            print(f"[FEHLER] Logo konnte nicht geladen werden: {LOGO_FILE}")
            print(f"        Grund: {e}")
            self.logo_ok = False
            self.logo_variants = {}

    def _pick_logo_variant(self, desired_size: int):
        if not self.logo_ok:
            return None
        best_key = min(self.logo_sizes, key=lambda s: abs(s - desired_size))
        return self.logo_variants[best_key]

    def _load_player_image(self):
        """
        Spielerbild laden (PNG).
        Skaliert grob auf PLAYER_TARGET_W und halbiert danach zusätzlich (max. 50%).
        """
        self.player_img = None
        self.player_w = 90
        self.player_h = 18

        try:
            base = tk.PhotoImage(file=PLAYER_FILE)
            bw = base.width()

            # 1) grob auf Zielbreite
            if bw >= PLAYER_TARGET_W:
                f = max(1, round(bw / PLAYER_TARGET_W))
                img = base.subsample(f, f)
            else:
                f = max(1, round(PLAYER_TARGET_W / bw))
                img = base.zoom(f, f)

            # 2) zusätzlich halbieren
            if PLAYER_EXTRA_HALVE:
                img = img.subsample(2, 2)

            self.player_img = img
            self.player_w = img.width()
            self.player_h = img.height()

        except Exception as e:
            print(f"[FEHLER] Spielerbild konnte nicht geladen werden: {PLAYER_FILE}")
            print(f"        Grund: {e}")
            self.player_img = None
            self.player_w = 90
            self.player_h = 18

    # -------------------------
    # Szene / UI
    # -------------------------

    def _init_scene(self):
        self.canvas.delete("all")

        # Raster
        for x in range(0, WIDTH, 24):
            self.canvas.create_line(x, 0, x, HEIGHT, fill=GRID)
        for y in range(0, HEIGHT, 24):
            self.canvas.create_line(0, y, WIDTH, y, fill=GRID)

        # Sternfeld (3 Ebenen)
        self.stars = []
        self.star_ids = []
        for layer in range(3):
            count = 38 if layer == 0 else (26 if layer == 1 else 18)
            speed = 35 + layer * 55
            size_min = 1 + layer
            size_max = 2 + layer
            for _ in range(count):
                sx = random.randint(0, WIDTH)
                sy = random.randint(0, HEIGHT)
                r = random.randint(size_min, size_max)
                sid = self.canvas.create_oval(
                    sx - r, sy - r, sx + r, sy + r,
                    fill=("#232A45" if layer == 0 else ("#2D3660" if layer == 1 else "#3C4A85")),
                    outline=""
                )
                self.stars.append({"x": sx, "y": sy, "r": r, "vy": speed})
                self.star_ids.append(sid)

        # HUD
        self.hud_id = self.canvas.create_text(
            12, 10, anchor="nw", fill=FG, font=("Consolas", 14),
            text=""
        )

        # Sprint-Leiste
        self.dash_bar_bg = self.canvas.create_rectangle(12, 36, 172, 48, fill="#0B0E19", outline="#222A44")
        self.dash_bar_fg = self.canvas.create_rectangle(12, 36, 12, 48, fill=ACCENT2, outline="")
        self.canvas.create_text(178, 42, anchor="w", fill=HUD_DIM, font=("Consolas", 11), text="SPRINT")

        self.overlay_items = []

    def _clear_overlay(self):
        for item in self.overlay_items:
            self.canvas.delete(item)
        self.overlay_items = []

    def _to_menu(self):
        self.state = "menu"
        self._clear_overlay()

        title = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 - 85,
            fill=FG, font=("Consolas", 40, "bold"),
            text="AUSWEICHEN"
        )

        instr = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 + 20,
            fill=FG, font=("Consolas", 12),
            text="ENTER = Start | ←/→ oder A/D = Bewegen | LEERTASTE = Sprint",
            width=WIDTH - 80, justify="center"
        )
        instr2 = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 + 50,
            fill=HUD_DIM, font=("Consolas", 11),
            text="P = Pause | R = Neustart | ESC = Beenden | Knapp vorbei = Bonus + Multiplikator",
            width=WIDTH - 80, justify="center"
        )

        best = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 + 95,
            fill=ACCENT, font=("Consolas", 15, "bold"),
            text=f"Bestwert: {self.best:.1f}"
        )

        self.overlay_items += [title, instr, instr2, best]

        self._reset_run_objects(create_player=True)
        self._update_hud(0.0)

    def _start_from_menu(self):
        if self.state == "menu":
            self.start()

    def start(self):
        self._clear_overlay()
        self._reset_run_objects(create_player=True)
        self.state = "playing"
        self.start_time = time.perf_counter()
        self.last_t = time.perf_counter()

    def _restart(self):
        if self.state in ("gameover", "paused", "playing"):
            self.start()
        else:
            self._to_menu()

    def _toggle_pause(self):
        if self.state == "playing":
            self.state = "paused"
            self._show_pause()
        elif self.state == "paused":
            self._clear_overlay()
            self.state = "playing"
            self.last_t = time.perf_counter()

    def _show_pause(self):
        self._clear_overlay()
        dim = self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#000000", outline="", stipple="gray50")
        t = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 - 10,
            fill=FG, font=("Consolas", 38, "bold"),
            text="PAUSE"
        )
        h = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 + 35,
            fill=FG, font=("Consolas", 13),
            text="P = Weiter | R = Neustart | ESC = Beenden",
            width=WIDTH - 80, justify="center"
        )
        self.overlay_items += [dim, t, h]

    def _game_over(self, score: float):
        self.state = "gameover"
        self._clear_overlay()

        if score > self.best:
            self.best = score
            self._save_best()

        dim = self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#000000", outline="", stipple="gray50")
        t = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 - 55,
            fill=FG, font=("Consolas", 44, "bold"),
            text="GAME OVER"
        )
        s = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 + 5,
            fill=FG, font=("Consolas", 18),
            text=f"Punkte: {score:.1f}   Bestwert: {self.best:.1f}"
        )
        h = self.canvas.create_text(
            WIDTH // 2, HEIGHT // 2 + 50,
            fill=FG, font=("Consolas", 13),
            text="R = Neustart | ENTER = Menü | ESC = Beenden",
            width=WIDTH - 80, justify="center"
        )
        self.overlay_items += [dim, t, s, h]

    # -------------------------
    # Spieler / Gegner / Popups
    # -------------------------

    def _reset_run_objects(self, create_player: bool):
        # Gegner + Popups entfernen
        if hasattr(self, "enemies"):
            for m in self.enemies:
                self.canvas.delete(m["id"])
        if hasattr(self, "popups"):
            for p in self.popups:
                self.canvas.delete(p["id"])

        # Spieler entfernen
        if hasattr(self, "player_id"):
            self.canvas.delete(self.player_id)

        # Schatten entfernen
        if hasattr(self, "player_shadow_ids"):
            for sid in self.player_shadow_ids:
                self.canvas.delete(sid)

        self.enemies = []
        self.popups = []
        self.player_shadow_ids = []

        # Lauf-Stats
        self.points = 0.0
        self.mult = 1.0

        # Schwierigkeit
        self.spawn_rate = SPAWN_RATE_START
        self.spawn_accel = SPAWN_ACCEL
        self.enemy_base_speed = ENEMY_BASE_SPEED
        self.enemy_speed_accel = ENEMY_SPEED_ACCEL

        # Bewegung
        self.player_x = WIDTH // 2
        self.player_vx = 0.0

        # Sprint
        self.dash_cd = DASH_CD
        self.dash_time = DASH_TIME
        self.dash_speed = DASH_SPEED
        self.dash_ready_t = 0.0
        self.dash_active_until = 0.0

        if create_player:
            self._create_player()

    def _create_player(self):
        x = self.player_x
        y = PLAYER_Y

        # Eleganter Schatten ohne Kastenoptik (weiche Ovale, kein Rechteck)
        self.player_shadow_ids = []

        if self.player_img is not None:
            w = self.player_w
            h = self.player_h

            # Sehr weicher Schatten (2 Lagen) leicht nach unten/rechts
            s2 = self.canvas.create_oval(
                x - w / 2 + 2, y + 12,
                x + w / 2 + 18, y + h + 20,
                fill="#000000", outline="", stipple="gray75"
            )
            s1 = self.canvas.create_oval(
                x - w / 2 + 6, y + 8,
                x + w / 2 + 14, y + h + 16,
                fill="#000000", outline="", stipple="gray50"
            )
            self.player_shadow_ids = [s2, s1]

            # Spielerbild
            self.player_id = self.canvas.create_image(x, y, image=self.player_img, anchor="n")
        else:
            # Fallback: Balken ohne Schatten
            self.player_id = self.canvas.create_rectangle(
                x - self.player_w / 2, y, x + self.player_w / 2, y + self.player_h,
                fill=ACCENT, outline=""
            )

    def _player_bbox(self):
        return self.canvas.bbox(self.player_id)

    def _move_player_to_x(self, new_x):
        half = self.player_w / 2
        new_x = clamp(new_x, half + 10, WIDTH - half - 10)
        dx = new_x - self.player_x
        self.player_x = new_x

        self.canvas.move(self.player_id, dx, 0)
        for sid in getattr(self, "player_shadow_ids", []):
            self.canvas.move(sid, dx, 0)

    def _set_dir(self, which: str, state: bool):
        if which == "L":
            self.left = state
        else:
            self.right = state

    def _dash(self):
        if self.state != "playing":
            return
        now = time.perf_counter()
        if now < self.dash_ready_t:
            return

        # Richtung: Eingabe bevorzugen, sonst aktuelle Geschwindigkeit
        if self.left and not self.right:
            dir_ = -1
        elif self.right and not self.left:
            dir_ = 1
        else:
            if self.player_vx < 0:
                dir_ = -1
            elif self.player_vx > 0:
                dir_ = 1
            else:
                dir_ = random.choice([-1, 1])

        self.dash_active_until = now + self.dash_time
        self.player_vx = dir_ * self.dash_speed
        self.dash_ready_t = now + self.dash_cd

        self._popup_text(self.player_x, PLAYER_Y - 18, "SPRINT", ACCENT2, ttl=0.35)

    def _spawn_enemy_logo(self, elapsed: float):
        desired = random.randint(LOGO_MIN, LOGO_MAX)
        img = self._pick_logo_variant(desired)

        vy = self.enemy_base_speed + self.enemy_speed_accel * elapsed + random.randint(-30, 70)

        if img is None:
            # Fallback: Kreis
            w = h = desired
            x = random.randint(10, WIDTH - 10 - w)
            y = -h - random.randint(0, 80)
            enemy_id = self.canvas.create_oval(x, y, x + w, y + h, fill="#FF5C7A", outline="")
            self.enemies.append({"id": enemy_id, "vy": vy, "y": y, "grazed": False})
            return

        w = img.width()
        h = img.height()

        x = random.randint(10, WIDTH - 10 - w)
        y = -h - random.randint(0, 80)

        enemy_id = self.canvas.create_image(x + w / 2, y + h / 2, image=img)

        # Referenz auf img behalten (gegen Garbage Collection)
        self.enemies.append({"id": enemy_id, "vy": vy, "y": y, "grazed": False, "img": img})

    def _popup_text(self, x, y, text, color, ttl=0.9):
        pid = self.canvas.create_text(x, y, fill=color, font=("Consolas", 14, "bold"), text=text)
        self.popups.append({"id": pid, "vy": -45.0, "t0": time.perf_counter(), "ttl": ttl})

    # -------------------------
    # HUD + Hintergrund
    # -------------------------

    def _update_starfield(self, dt):
        for s, sid in zip(self.stars, self.star_ids):
            s["y"] += s["vy"] * dt
            if s["y"] > HEIGHT + 10:
                s["y"] = -10
                s["x"] = random.randint(0, WIDTH)
            r = s["r"]
            self.canvas.coords(sid, s["x"] - r, s["y"] - r, s["x"] + r, s["y"] + r)

    def _update_hud(self, elapsed):
        score = max(0.0, elapsed + self.points)
        line1 = f"Punkte: {score:6.1f}   Multi: {self.mult:4.2f}   Bestwert: {self.best:6.1f}"
        line2 = f"Logos: {len(self.enemies):3d}   (Knapp vorbei = Bonus)"
        self.canvas.itemconfig(self.hud_id, text=f"{line1}\n{line2}")

        # Sprint-Leiste
        now = time.perf_counter()
        if self.state != "playing":
            frac = 1.0
        else:
            if now >= self.dash_ready_t:
                frac = 1.0
            else:
                frac = clamp(1.0 - ((self.dash_ready_t - now) / self.dash_cd), 0.0, 1.0)

        x0, y0, x1, y1 = self.canvas.coords(self.dash_bar_bg)
        fill_x = x0 + (x1 - x0) * frac
        self.canvas.coords(self.dash_bar_fg, x0, y0, fill_x, y1)

    # -------------------------
    # Loop
    # -------------------------

    def _tick(self):
        now = time.perf_counter()
        dt = now - self.last_t
        self.last_t = now
        dt = min(dt, 0.05)

        # Hintergrund läuft immer
        self._update_starfield(dt)

        if self.state == "playing":
            elapsed = now - self.start_time

            # Schwierigkeit steigt
            self.spawn_rate += self.spawn_accel * dt

            # Spawn
            p = self.spawn_rate * dt
            while p > 1.0:
                self._spawn_enemy_logo(elapsed)
                p -= 1.0
            if random.random() < p:
                self._spawn_enemy_logo(elapsed)

            # Multiplikator fällt langsam zurück
            self.mult = max(1.0, self.mult - MULT_DECAY * dt)

            # Bewegung (Beschleunigung + Reibung)
            target = 0.0
            if self.left and not self.right:
                target = -PLAYER_MAX_SPEED
            elif self.right and not self.left:
                target = PLAYER_MAX_SPEED

            if now < self.dash_active_until:
                pass
            else:
                if target != 0.0:
                    if self.player_vx < target:
                        self.player_vx = min(target, self.player_vx + PLAYER_ACCEL * dt)
                    elif self.player_vx > target:
                        self.player_vx = max(target, self.player_vx - PLAYER_ACCEL * dt)
                else:
                    if self.player_vx > 0:
                        self.player_vx = max(0.0, self.player_vx - PLAYER_FRICTION * dt)
                    elif self.player_vx < 0:
                        self.player_vx = min(0.0, self.player_vx + PLAYER_FRICTION * dt)

            self._move_player_to_x(self.player_x + self.player_vx * dt)

            # Gegner bewegen + Kollision + "knapp vorbei"
            alive = []
            pb = self._player_bbox()
            graze_box = (pb[0] - GRAZE_MARGIN, pb[1] - GRAZE_MARGIN,
                         pb[2] + GRAZE_MARGIN, pb[3] + GRAZE_MARGIN)

            for m in self.enemies:
                dy = m["vy"] * dt
                m["y"] += dy
                self.canvas.move(m["id"], 0, dy)

                mb = self.canvas.bbox(m["id"])
                if mb:
                    # Kollision
                    if aabb_intersect(pb, mb):
                        score = (now - self.start_time) + self.points
                        self._game_over(score)
                        break

                    # knapp vorbei (ohne Kollision)
                    if (not m.get("grazed", False)) and aabb_intersect(graze_box, mb):
                        m["grazed"] = True
                        gain = GRAZE_BONUS * self.mult
                        self.points += gain
                        self.mult = min(6.0, self.mult + GRAZE_MULT_GAIN)

                        cx = (mb[0] + mb[2]) / 2
                        cy = (mb[1] + mb[3]) / 2
                        self._popup_text(cx, cy - 18, f"+{gain:.0f}", ACCENT, ttl=0.8)

                if m["y"] < HEIGHT + 140:
                    alive.append(m)
                else:
                    self.canvas.delete(m["id"])

            if self.state == "playing":
                self.enemies = alive

            # Popups animieren
            new_pop = []
            for p in self.popups:
                age = now - p["t0"]
                if age <= p["ttl"]:
                    self.canvas.move(p["id"], 0, p["vy"] * dt)
                    new_pop.append(p)
                else:
                    self.canvas.delete(p["id"])
            self.popups = new_pop

            self._update_hud(now - self.start_time)

        elif self.state == "menu":
            self._update_hud(0.0)

        elif self.state in ("paused", "gameover"):
            self._update_hud(time.perf_counter() - getattr(self, "start_time", time.perf_counter()))

        # ENTER: im Game Over zurück ins Menü
        if not hasattr(self, "_return_bound"):
            self._return_bound = True
            self.root.bind("<KeyPress-Return>", self._return_dispatch)

        self.root.after(FRAME_MS, self._tick)

    def _return_dispatch(self, e=None):
        if self.state == "menu":
            self.start()
        elif self.state == "gameover":
            self._to_menu()


def main():
    root = tk.Tk()
    Game(root)
    root.mainloop()


if __name__ == "__main__":
    main()
