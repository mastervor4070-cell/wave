# Wave

Лёгкий музыкальный плеер для Linux: поиск и воспроизведение треков с SoundCloud и YouTube, персональная «Волна», избранное и статистика вкуса.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![GTK4](https://img.shields.io/badge/GTK-4-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

Один файл, ~300 строк, без Electron и без браузера. Интерфейс — GTK4 + libadwaita, воспроизведение — mpv через IPC-сокет, поиск и стриминг — yt-dlp.

## Возможности

- **Поиск** по SoundCloud и YouTube прямо из окна, воспроизведение потоком без скачивания.
- **Волна** — бесконечный поток: собирает артистов из истории прослушиваний и лайков, ищет похожее и подмешивает новое.
- **Избранное** — сохраняется в SQLite, переживает перезапуск.
- **Вкус** — статистика: часы прослушивания, топ-артисты, распределение по платформам, любимый час суток.
- **Импорт из Spotify** — CSV из [Exportify](https://exportify.net/): треки добавляются в избранное, а при воспроизведении находятся на YouTube (Spotify-DRM не играется, поэтому ищем тот же трек в открытых источниках).
- Плеер-бар: плей/пауза, следующий/предыдущий, перемотка, громкость.

## Требования

| Зависимость | Зачем |
|---|---|
| Python 3.11+ | сам плеер |
| GTK 4 + libadwaita | интерфейс |
| PyGObject (`python-gobject`) | привязки GTK для Python |
| mpv | воспроизведение |
| yt-dlp | поиск и стриминг |

Установка по дистрибутивам:

```bash
# Arch
sudo pacman -S python-gobject gtk4 libadwaita mpv yt-dlp

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 mpv yt-dlp

# Fedora
sudo dnf install python3-gobject gtk4 libadwaita mpv yt-dlp
```

## Запуск

```bash
git clone https://github.com/mastervor4070-cell/wave.git
cd wave
python3 wave.py
```

Или через ярлык: скопировать `wave` и `wave.desktop` в `~/.local/bin` и
`~/.local/share/applications` — в меню появится «Wave».

## Данные

Всё локально, ничего не отправляется наружу:

- `~/.local/share/wave/wave.db` — избранное и история прослушиваний (SQLite);
- `$XDG_RUNTIME_DIR/wave-mpv.sock` — IPC-сокет mpv (создаётся при запуске).

Удалить статистику — просто удалить `wave.db`.

## Как это работает

1. `yt-dlp -J --flat-playlist` отдаёт JSON с результатами поиска — из него берутся название, артист и URL.
2. URL передаётся в mpv через `loadfile`, управление — командами по JSON-IPC (`cycle pause`, `seek`, `set_property volume`).
3. События mpv (`time-pos`, `duration`, `pause`, `end-file`) возвращаются в GTK-цикл через `GLib.idle_add`.
4. «Волна» считает вес артиста как сумму секунд прослушивания + 2 минуты за каждый лайк, берёт топ-12, случайно выбирает 3 затравки и ищет по ним треки, выкидывая уже слышанное.

## Ограничения

- Нужен интернет: треки не кэшируются на диск.
- SoundCloud/YouTube могут менять API — тогда помогает `yt-dlp -U`.
- Импорт Spotify — только метаданные из CSV, не аудио.

## Лицензия

MIT — см. [LICENSE](LICENSE).
