#!/usr/bin/env python3
"""Wave — лёгкий мульти-платформенный плеер (GTK4 + libadwaita + mpv + yt-dlp)."""
import csv, json, os, random, socket, sqlite3, subprocess, threading, time
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

DB_PATH = os.path.join(GLib.get_user_data_dir(), "wave", "wave.db")
SOCK = os.path.join(GLib.get_user_runtime_dir() or "/tmp", "wave-mpv.sock")
CSS = b"""
.now {padding:6px 12px 2px 12px}
.now .t {font-weight:700}
.now .a {opacity:.65; font-size:.85em}
scale trough {min-height:3px}
"""


class DB:
    def __init__(s):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        s.c = sqlite3.connect(DB_PATH, check_same_thread=False); s.l = threading.Lock()
        s.q("CREATE TABLE IF NOT EXISTS fav(url TEXT PRIMARY KEY,title,artist,source,dur)", w=1)
        s.q("CREATE TABLE IF NOT EXISTS plays(url,title,artist,source,ts INT,secs REAL)", w=1)

    def q(s, sql, a=(), w=0):
        with s.l:
            r = s.c.execute(sql, a).fetchall()
            if w: s.c.commit()
            return r

    def is_fav(s, u): return bool(s.q("SELECT 1 FROM fav WHERE url=?", (u,)))

    def add_fav(s, t):
        s.q("INSERT OR IGNORE INTO fav VALUES(?,?,?,?,?)",
            (t["url"], t["title"], t["artist"], t["source"], t["dur"]), w=1)

    def toggle_fav(s, t):
        if s.is_fav(t["url"]): s.q("DELETE FROM fav WHERE url=?", (t["url"],), w=1); return False
        s.add_fav(t); return True

    def favs(s):
        return [dict(url=r[0], title=r[1], artist=r[2], source=r[3], dur=r[4])
                for r in s.q("SELECT * FROM fav ORDER BY rowid DESC")]

    def log(s, t, secs):
        s.q("INSERT INTO plays VALUES(?,?,?,?,?,?)",
            (t["url"], t["title"], t["artist"], t["source"], int(time.time()), secs), w=1)

    def recent(s, n=150): return {r[0] for r in s.q("SELECT url FROM plays ORDER BY ts DESC LIMIT ?", (n,))}

    def artists(s):
        """Вес артиста = секунды прослушивания + 2 мин за каждый лайк."""
        w = {}
        for a, x in s.q("SELECT artist,SUM(secs) FROM plays GROUP BY artist"): w[a] = w.get(a, 0) + x
        for (a,) in s.q("SELECT artist FROM fav"): w[a] = w.get(a, 0) + 120
        return sorted(w.items(), key=lambda i: -i[1])

    def stats(s):
        tot = s.q("SELECT COALESCE(SUM(secs),0),COUNT(*) FROM plays")[0]
        src = s.q("SELECT source,SUM(secs) FROM plays GROUP BY source ORDER BY 2 DESC")
        hr = s.q("SELECT strftime('%H',ts,'unixepoch','localtime') h,SUM(secs) FROM plays GROUP BY h ORDER BY 2 DESC LIMIT 1")
        return tot, src, hr


def search(q, src="sc", n=15):
    pre = {"sc": "scsearch", "yt": "ytsearch"}[src]
    out = subprocess.run(["yt-dlp", "-J", "--flat-playlist", "--no-warnings", f"{pre}{n}:{q}"],
                         capture_output=True, text=True, timeout=45).stdout
    res = []
    for e in json.loads(out or "{}").get("entries", []):
        u = e.get("url") or e.get("webpage_url")
        if u:
            res.append(dict(url=u, title=e.get("title") or "?", source=src, dur=e.get("duration") or 0,
                            artist=e.get("uploader") or e.get("channel") or "—"))
    return res


def bg(fn, cb):
    def w():
        try: r = fn()
        except Exception: r = None
        GLib.idle_add(lambda: (cb(r), False)[1])
    threading.Thread(target=w, daemon=True).start()


class Player:
    def __init__(s, on_event):
        s.cb = on_event
        if os.path.exists(SOCK): os.remove(SOCK)
        s.p = subprocess.Popen(["mpv", "--idle=yes", "--no-video", "--no-terminal", "--force-window=no",
                                "--ytdl-format=bestaudio/best", "--input-ipc-server=" + SOCK])
        s.s = None
        for _ in range(60):
            try:
                x = socket.socket(socket.AF_UNIX); x.connect(SOCK); s.s = x; break
            except OSError: time.sleep(0.1)
        threading.Thread(target=s._read, daemon=True).start()
        for i, n in enumerate(("time-pos", "duration", "pause")): s.cmd("observe_property", i + 1, n)

    def cmd(s, *a):
        if s.s: s.s.send((json.dumps({"command": list(a)}) + "\n").encode())

    def _read(s):
        buf = b""
        while s.s:
            d = s.s.recv(65536)
            if not d: break
            buf += d
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try: ev = json.loads(line)
                except ValueError: continue
                if "event" in ev: GLib.idle_add(lambda e=ev: (s.cb(e), False)[1])

    def quit(s): s.p.terminate()


class Win(Adw.ApplicationWindow):
    def __init__(s, app):
        super().__init__(application=app, default_width=400, default_height=660, title="Wave")
        s.db = DB(); s.queue = []; s.idx = -1; s.cur = None; s.pos = 0; s.wave = False
        s.player = Player(s.on_event)
        s.connect("close-request", lambda *_: (s.log_play(), s.player.quit(), False)[2])

        s.stack = Adw.ViewStack()
        s.stack.add_titled_with_icon(s.page_search(), "s", "Поиск", "system-search-symbolic")
        s.stack.add_titled_with_icon(s.page_wave(), "w", "Волна", "media-playlist-shuffle-symbolic")
        s.stack.add_titled_with_icon(s.page_favs(), "f", "Избранное", "starred-symbolic")
        s.stack.add_titled_with_icon(s.page_stats(), "a", "Вкус", "utilities-system-monitor-symbolic")
        s.stack.connect("notify::visible-child", s.on_page)

        bar = Adw.ViewSwitcherBar(stack=s.stack, reveal=True)
        tv = Adw.ToolbarView(content=s.stack)
        hb = Adw.HeaderBar(); hb.set_title_widget(Gtk.Label(label="Wave", css_classes=["heading"]))
        tv.add_top_bar(hb); tv.add_bottom_bar(s.player_bar()); tv.add_bottom_bar(bar)
        s.set_content(tv)
        s.refresh_favs()

    # ---------- UI ----------
    def mklist(s):
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, show_separators=True)
        sw = Gtk.ScrolledWindow(vexpand=True, child=lb, hscrollbar_policy=Gtk.PolicyType.NEVER)
        return sw, lb

    def page_search(s):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        s.entry = Gtk.SearchEntry(placeholder_text="Артист или трек…", hexpand=True)
        s.entry.connect("activate", s.do_search)
        s.src = Gtk.DropDown.new_from_strings(["SoundCloud", "YouTube"])
        row = Gtk.Box(spacing=6, margin_start=8, margin_end=8)
        row.append(s.entry); row.append(s.src); box.append(row)
        sw, s.res = s.mklist(); box.append(sw)
        return box

    def page_wave(s):
        s.wbtn = Gtk.Button(label="Включить", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        s.wbtn.connect("clicked", lambda *_: s.start_wave())
        return Adw.StatusPage(icon_name="audio-headphones-symbolic", title="Моя волна",
                              description="Бесконечный поток по твоим прослушиваниям и избранному", child=s.wbtn)

    def page_favs(s):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        b = Gtk.Button(label="Импорт Spotify (CSV из Exportify)", css_classes=["flat"], margin_start=8, margin_end=8)
        b.connect("clicked", lambda *_: Gtk.FileDialog().open(s, None, s.imp_done))
        box.append(b); sw, s.favl = s.mklist(); box.append(sw)
        return box

    def page_stats(s):
        s.sbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12,
                         margin_start=16, margin_end=16)
        return Gtk.ScrolledWindow(child=s.sbox)

    def player_bar(s):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["now"])
        top = Gtk.Box(spacing=4)
        tx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        s.lt = Gtk.Label(label="Ничего не играет", xalign=0, ellipsize=3, css_classes=["t"])
        s.la = Gtk.Label(label="", xalign=0, ellipsize=3, css_classes=["a"])
        tx.append(s.lt); tx.append(s.la); top.append(tx)
        def btn(icon, fn):
            b = Gtk.Button(icon_name=icon, css_classes=["flat", "circular"]); b.connect("clicked", fn); top.append(b); return b
        btn("media-skip-backward-symbolic", lambda *_: s.prev())
        s.pb = btn("media-playback-start-symbolic", lambda *_: s.player.cmd("cycle", "pause"))
        btn("media-skip-forward-symbolic", lambda *_: s.next())
        s.vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        s.vol.set_draw_value(False); s.vol.set_size_request(80, -1); s.vol.set_value(100)
        s.vol.set_valign(Gtk.Align.CENTER)
        s.vol.connect("value-changed", lambda w: s.player.cmd("set_property", "volume", w.get_value()))
        top.append(s.vol)
        s.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1); s.scale.set_draw_value(False)
        s.scale.connect("change-value", lambda w, st, v: (s.player.cmd("seek", v, "absolute"), False)[1])
        v.append(top); v.append(s.scale)
        return v

    def fill(s, lb, tracks):
        while r := lb.get_first_child(): lb.remove(r)
        for i, t in enumerate(tracks):
            row = Adw.ActionRow(title=GLib.markup_escape_text(t["title"]),
                                subtitle=GLib.markup_escape_text(t["artist"]), activatable=True)
            row.set_title_lines(1); row.set_subtitle_lines(1)
            b = Gtk.Button(icon_name="starred-symbolic" if s.db.is_fav(t["url"]) else "non-starred-symbolic",
                           valign=Gtk.Align.CENTER, css_classes=["flat"])
            b.connect("clicked", s.on_fav, t); row.add_suffix(b)
            row.connect("activated", lambda r, i=i, ts=tracks: s.play_queue(ts, i))
            lb.append(row)

    def on_fav(s, b, t):
        b.set_icon_name("starred-symbolic" if s.db.toggle_fav(t) else "non-starred-symbolic")
        s.refresh_favs()

    def refresh_favs(s):
        s.fav_tracks = s.db.favs(); s.fill(s.favl, s.fav_tracks)

    def do_search(s, e):
        q = e.get_text().strip()
        if not q: return
        src = "sc" if s.src.get_selected() == 0 else "yt"
        bg(lambda: search(q, src), lambda r: (setattr(s, "found", r or []), s.fill(s.res, s.found)))

    def imp_done(s, d, res):
        try: f = d.open_finish(res)
        except GLib.Error: return
        with open(f.get_path(), newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                r = {(k or "").lower(): v for k, v in r.items()}
                title = r.get("track name") or r.get("title") or ""
                art = (r.get("artist name(s)") or r.get("artist name") or r.get("artist") or "").split(";")[0]
                if title:  # Spotify-DRM не играем: ищем такой же трек на YouTube
                    s.db.add_fav(dict(url=f"ytdl://ytsearch1:{art} {title}", title=title,
                                      artist=art or "—", source="spotify", dur=0))
        s.refresh_favs()

    def on_page(s, *_):
        if s.stack.get_visible_child_name() != "a": return
        while c := s.sbox.get_first_child(): s.sbox.remove(c)
        (secs, n), src, hr = s.db.stats()
        def lab(t, cls=None): s.sbox.append(Gtk.Label(label=t, xalign=0, css_classes=[cls] if cls else []))
        lab(f"{secs/3600:.1f} ч · {n} треков", "title-2")
        if hr: lab(f"Чаще всего слушаешь около {hr[0][0]}:00", "dim-label")
        arts = s.db.artists()[:6]
        if arts:
            lab("Топ артистов", "heading")
            for a, w in arts:
                lab(a)
                s.sbox.append(Gtk.LevelBar(min_value=0, max_value=arts[0][1], value=w))
        if src:
            lab("Платформы", "heading")
            tot = sum(x for _, x in src) or 1
            for nme, x in src: lab(f"{nme}: {100*x/tot:.0f}%")
        if not arts: lab("Послушай что-нибудь или добавь в избранное — тут появится твой вкус.", "dim-label")

    # ---------- Playback ----------
    def play_queue(s, tracks, i, wave=False):
        s.queue = list(tracks); s.idx = i; s.wave = wave; s.start()

    def start(s):
        s.log_play(); t = s.queue[s.idx]; s.cur = t; s.pos = 0
        s.lt.set_label(t["title"]); s.la.set_label(f'{t["artist"]} · {t["source"]}')
        s.player.cmd("loadfile", t["url"]); s.player.cmd("set_property", "pause", False)

    def log_play(s):
        if s.cur and s.pos > 20: s.db.log(s.cur, s.pos)
        s.cur = None

    def next(s):
        if s.idx + 1 < len(s.queue): s.idx += 1; s.start()
        elif s.wave: s.start_wave()
        else: s.log_play()

    def prev(s):
        if s.pos > 5 or s.idx == 0: s.player.cmd("seek", 0, "absolute")
        else: s.idx -= 1; s.start()

    def start_wave(s):
        s.wbtn.set_label("Подбираю…"); s.wbtn.set_sensitive(False)
        def gen():
            pool = [a for a, w in s.db.artists()[:12]] or random.sample(["lofi", "indie", "ambient", "synthwave"], 2)
            seeds = list(dict.fromkeys(random.sample(pool, min(3, len(pool)))))
            seen, out = s.db.recent(), []
            for a in seeds: out += search(a, "sc", 8)
            out = [t for t in out if t["url"] not in seen]; random.shuffle(out)
            return out[:20]
        def done(r):
            s.wbtn.set_label("Включить"); s.wbtn.set_sensitive(True)
            if r: s.play_queue(r, 0, wave=True)
        bg(gen, done)

    def on_event(s, ev):
        e = ev["event"]
        if e == "property-change":
            n, d = ev.get("name"), ev.get("data")
            if n == "time-pos" and d is not None: s.pos = d; s.scale.set_value(d)
            elif n == "duration" and d: s.scale.set_range(0, d)
            elif n == "pause":
                s.pb.set_icon_name("media-playback-start-symbolic" if d else "media-playback-pause-symbolic")
        elif e == "end-file" and ev.get("reason") in ("eof", "error"):
            s.next()


class App(Adw.Application):
    def __init__(s):
        super().__init__(application_id="dev.local.Wave", flags=Gio.ApplicationFlags.FLAGS_NONE)
    def do_activate(s):
        from gi.repository import Gdk
        p = Gtk.CssProvider(); p.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        (s.props.active_window or Win(s)).present()


if __name__ == "__main__":
    App().run()
