import json
import os
import sys
import subprocess

from collections import OrderedDict
from PySide6.QtCore import Qt, QSize, QTimer, QProcess
from PySide6.QtGui import QAction, QIcon, QPixmap, QImageReader
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QListWidget, QListWidgetItem, QListView,
    QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QSplitter, QMessageBox, QStyle, QSlider, QStatusBar, QWidget
)

import vlc


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller bundle."""
    if hasattr(sys, '_MEIPASS'):
        # Running from a PyInstaller bundle
        return os.path.join(sys._MEIPASS, relative_path)
    else:
        # Running in normal Python
        return os.path.join(os.path.abspath("."), relative_path)


class Episode:
    def __init__(self, title, path, is_external_exe=False,
                 subtitle_path=None, thumbnail_path=None):
        self.title = title
        self.path = path
        self.is_external_exe = is_external_exe
        self.subtitle_path = subtitle_path
        self.thumbnail_path = thumbnail_path

    def resolved_path(self):
        return resource_path(self.path)

    def resolved_subtitle(self):
        if not self.subtitle_path:
            return None
        p = resource_path(self.subtitle_path)
        if os.path.exists(p):
            return p
        # Try normalized path
        try:
            norm = os.path.normpath(p)
            if os.path.exists(norm):
                return norm
        except Exception:
            pass
        # Try common alternate locations and case-insensitive matches
        try:
            dirname = os.path.dirname(p)
            basename = os.path.basename(p)
            candidates = [
                os.path.join(dirname, basename),
                os.path.join(dirname, 'captions', basename),
                os.path.join(dirname, 'subtitles', basename),
            ]
            for c in candidates:
                if os.path.exists(c):
                    return c
            # Case-insensitive search in dirname and captions folder
            for d in [dirname, os.path.join(dirname, 'captions')]:
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        if f.lower() == basename.lower():
                            return os.path.join(d, f)
        except Exception:
            pass
        return p

    def resolved_thumbnail(self):
        if not self.thumbnail_path:
            return None
        p = resource_path(self.thumbnail_path)
        if os.path.exists(p):
            return p
        # Common typo fallback: 'thumnails' -> 'thumbnails'
        alt = p.replace('thumnails', 'thumbnails')
        if os.path.exists(alt):
            return alt
        # Try normalized path variations
        try:
            norm = os.path.normpath(p)
            if os.path.exists(norm):
                return norm
        except Exception:
            pass
        # Try inserting a 'thumbnails' (or common-typo) subfolder next to the given path
        try:
            dirname = os.path.dirname(p)
            basename = os.path.basename(p)
            candidates = [
                os.path.join(dirname, 'thumbnails', basename),
                os.path.join(dirname, 'thumnails', basename),
                os.path.join(dirname, basename),
            ]
            for c in candidates:
                if os.path.exists(c):
                    return c

            # Case-insensitive match in the same directory
            if os.path.isdir(dirname):
                for f in os.listdir(dirname):
                    if f.lower() == basename.lower():
                        return os.path.join(dirname, f)
                # Also search inside an actual 'thumbnails' folder if present
                thumbs = os.path.join(dirname, 'thumbnails')
                if os.path.isdir(thumbs):
                    for f in os.listdir(thumbs):
                        if f.lower() == basename.lower():
                            return os.path.join(thumbs, f)
        except Exception:
            pass
        return p


class Season:
    def __init__(self, title, episodes, logo_path=None):
        self.title = title
        self.episodes = episodes
        self.logo_path = logo_path

    def resolved_logo(self):
        return resource_path(self.logo_path) if self.logo_path else None


class DvdStylePlayer(QMainWindow):
    def __init__(self, seasons):
        super().__init__()
        self.setWindowTitle("DVD-Style Menu")
        self.resize(1200, 750)

        # Left panel: seasons + episodes
        splitter = QSplitter(Qt.Horizontal)
        self.splitter = splitter
        self.season_list = QListWidget()
        self.episode_list = QListWidget()

        # Make icons larger so thumbnails/logos are visible
        # Use IconMode so icons are shown above text; make all icons the same width.
        self.season_list.setViewMode(QListView.IconMode)
        self.episode_list.setViewMode(QListView.IconMode)

        self.ICON_WIDTH = 240
        self.ICON_HEIGHT = 135
        self.season_list.setIconSize(QSize(self.ICON_WIDTH, self.ICON_HEIGHT))
        self.episode_list.setIconSize(QSize(self.ICON_WIDTH, self.ICON_HEIGHT))
        self.season_list.setGridSize(QSize(self.ICON_WIDTH + 20, self.ICON_HEIGHT + 50))
        self.episode_list.setGridSize(QSize(self.ICON_WIDTH + 20, self.ICON_HEIGHT + 50))

        splitter.addWidget(self.season_list)
        splitter.addWidget(self.episode_list)
        splitter.setSizes([280, 380])

        # Right panel: video surface + preview + controls
        self.video_surface = QWidget()
        self.video_surface.setStyleSheet("background-color: black;")

        self.preview_label = QLabel("Episode Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(150)

        controls = self._build_controls()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self.video_surface)
        right_layout.addWidget(self.preview_label)
        right_layout.addLayout(controls)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(splitter)
        layout.addWidget(right_panel)
        self.setCentralWidget(container)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # VLC setup
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_player_new()
        try:
            win_id = int(self.video_surface.winId())
            self.vlc_player.set_hwnd(win_id)  # Windows binding
        except Exception as e:
            QMessageBox.critical(self, "Video init error", f"Failed to bind video surface.\n{e}")

        self.current_episode = None
        self.subtitle_enabled = True
        # Panels visibility state
        self._panels_visible = True
        # Pixmap cache (LRU)
        self._pixmap_cache = OrderedDict()
        self._pixmap_cache_max = 200

        # UI sync timer
        self.ui_timer = QTimer(self)
        # Slightly reduce UI update frequency to lower CPU usage while keeping UI responsive
        self.ui_timer.setInterval(300)
        self.ui_timer.timeout.connect(self._sync_ui)

        # Build menu and populate data
        self._build_menu()
        self.seasons = seasons
        self._populate_seasons()

        # Attach VLC end-of-media event to handle when playback ends
        try:
            em = self.vlc_player.event_manager()
            em.event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: QTimer.singleShot(0, self._on_media_ended))
        except Exception:
            pass

        # Signals
        self.season_list.currentItemChanged.connect(self._on_season_selected)
        self.episode_list.currentItemChanged.connect(self._on_episode_selected)
        self.episode_list.itemDoubleClicked.connect(self._on_episode_double_clicked)

    def _build_controls(self):
        play_btn = QPushButton()
        play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        play_btn.clicked.connect(self._play_selected)

        pause_btn = QPushButton()
        pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        pause_btn.clicked.connect(self._pause)

        stop_btn = QPushButton()
        stop_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        stop_btn.clicked.connect(self._stop)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        # Make the runtime slider larger (stretch) and update during moves
        self.position_slider.sliderMoved.connect(self._on_slider_moved)

        # Time label showing current / total
        self.position_label = QLabel("00:00 / 00:00")

        vol_label = QLabel("Vol")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        # Make volume slider compact
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        # Subtitle toggle button (checkable)
        self.subtitle_button = QPushButton("Sub")
        self.subtitle_button.setCheckable(True)
        self.subtitle_button.setChecked(True)
        self.subtitle_button.setFixedWidth(60)
        # Connect button to toggle subtitles; use a small wrapper to pass the checked state
        self.subtitle_button.toggled.connect(lambda checked: self._toggle_subtitles(checked))

        controls = QHBoxLayout()
        controls.addWidget(play_btn)
        controls.addWidget(pause_btn)
        controls.addWidget(stop_btn)
        # Give runtime slider stretch so it becomes larger than the volume control
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.position_label)
        controls.addWidget(vol_label)
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.subtitle_button)
        return controls

    def _format_ms(self, ms: int) -> str:
        if ms is None or ms < 0:
            return "00:00"
        s = int(ms // 1000)
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h:
            return f"{h}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    def _load_scaled_pixmap(self, path: str, target_width: int) -> QPixmap:
        """
        Load an image using QImageReader and scale it as it's read to avoid
        consuming large amounts of memory (works around Qt QPixmap limits).
        """
        if not path or not os.path.exists(path):
            return QPixmap()
        # Use cache key (path, width) to avoid re-decoding large images repeatedly
        key = f"{os.path.abspath(path)}|{int(target_width)}"
        try:
            if key in self._pixmap_cache:
                # Move to end (most recently used)
                pm = self._pixmap_cache.pop(key)
                self._pixmap_cache[key] = pm
                return pm
        except Exception:
            pass
        try:
            reader = QImageReader(path)
            size = reader.size()
            if size.isValid() and size.width() > target_width:
                new_h = int(size.height() * (target_width / size.width()))
                reader.setScaledSize(QSize(target_width, new_h))
            img = reader.read()
            if img.isNull():
                return QPixmap()
            pm = QPixmap.fromImage(img)
            try:
                # store in LRU cache
                self._pixmap_cache[key] = pm
                if len(self._pixmap_cache) > self._pixmap_cache_max:
                    # pop oldest
                    self._pixmap_cache.popitem(last=False)
            except Exception:
                pass
            return pm
        except Exception:
            # Fallback: let QPixmap try, then scale down
            try:
                pm = QPixmap(path)
                scaled = pm.scaled(target_width, target_width, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                try:
                    self._pixmap_cache[key] = scaled
                    if len(self._pixmap_cache) > self._pixmap_cache_max:
                        self._pixmap_cache.popitem(last=False)
                except Exception:
                    pass
                return scaled
            except Exception:
                return QPixmap()

    def _build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("&View")

        self.subtitle_action = QAction("Subtitles", self, checkable=True)
        self.subtitle_action.setChecked(True)
        self.subtitle_action.triggered.connect(self._toggle_subtitles)
        view_menu.addAction(self.subtitle_action)

        fullscreen_action = QAction("Toggle Fullscreen", self, checkable=True)
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        # Panels visibility toggle
        self.panels_action = QAction("Hide Side Panels", self, checkable=True)
        self.panels_action.setChecked(False)
        self.panels_action.triggered.connect(lambda checked: self._set_panels_visible(not checked))
        view_menu.addAction(self.panels_action)

        help_menu = menubar.addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._about)
        help_menu.addAction(about_action)

    def _populate_seasons(self):
        self.season_list.clear()
        for season in self.seasons:
            # Put a blank line above the season title so text appears separated from icon
            item = QListWidgetItem("\n" + season.title)
            item.setData(Qt.UserRole, season)
            logo = season.resolved_logo()
            if logo and os.path.exists(logo):
                pixmap = self._load_scaled_pixmap(logo, self.ICON_WIDTH)
                if not pixmap.isNull():
                    icon = QIcon(pixmap)
                    item.setIcon(icon)
            # Ensure items have enough vertical space for icon + blank line + title
            item.setSizeHint(QSize(self.ICON_WIDTH + 20, self.ICON_HEIGHT + 50))
            self.season_list.addItem(item)
        if self.season_list.count() > 0:
            self.season_list.setCurrentRow(0)

    def _set_panels_visible(self, visible: bool):
        # The splitter is the left-side selection area; hiding it gives the video more room
        try:
            self._panels_visible = visible
            # splitter stored as the first widget added to central widget layout earlier
            # We saved splitter locally; ensure attribute exists
            if hasattr(self, 'splitter'):
                self.splitter.setVisible(visible)
            # Keep the menu action in sync (checked = panels hidden)
            try:
                if hasattr(self, 'panels_action'):
                    self.panels_action.setChecked(not visible)
            except Exception:
                pass
            else:
                # try to find splitter child
                for w in self.centralWidget().children():
                    if isinstance(w, QSplitter):
                        w.setVisible(visible)
                        break
        except Exception:
            pass

    def _on_season_selected(self, current, previous):
        self.episode_list.clear()
        if not current:
            return
        season = current.data(Qt.UserRole)
        for ep in season.episodes:
            # Show a blank line above the episode title for spacing under the icon
            label = "\n" + ep.title + ("  [Game]" if ep.is_external_exe else "")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, ep)
            thumb = ep.resolved_thumbnail()
            if thumb and os.path.exists(thumb):
                pixmap = self._load_scaled_pixmap(thumb, self.ICON_WIDTH)
                if not pixmap.isNull():
                    icon = QIcon(pixmap)
                    item.setIcon(icon)
            item.setSizeHint(QSize(self.ICON_WIDTH + 20, self.ICON_HEIGHT + 50))
            self.episode_list.addItem(item)
        if self.episode_list.count() > 0:
            self.episode_list.setCurrentRow(0)

    def _on_episode_selected(self, current, previous):
        if not current:
            return
        ep = current.data(Qt.UserRole)
        thumb = ep.resolved_thumbnail()
        # Make sure preview is visible when selecting
        self.preview_label.setVisible(True)
        if thumb and os.path.exists(thumb):
            pixmap = self._load_scaled_pixmap(thumb, self.preview_label.width())
            if not pixmap.isNull():
                self.preview_label.setPixmap(
                    pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                return
        # Fallback text when no preview image
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Episode Preview")

    def _on_episode_double_clicked(self, item):
        ep = item.data(Qt.UserRole)
        self._play_episode(ep)

    def _play_selected(self):
        item = self.episode_list.currentItem()
        if item:
            ep = item.data(Qt.UserRole)
            self._play_episode(ep)

    def _play_episode(self, ep):
        if ep.is_external_exe:
            exe_path = ep.resolved_path()
            if not os.path.exists(exe_path):
                QMessageBox.critical(self, "Missing game", f"Cannot find embedded game: {exe_path}")
                return
            # Stop any current playback and deselect the currently playing episode
            try:
                if self.vlc_player:
                    self._stop()
            except Exception:
                pass
            try:
                self.episode_list.clearSelection()
                self.episode_list.setCurrentRow(-1)
            except Exception:
                pass

            # Use QProcess so we can reliably get a finished signal and re-enable the UI
            try:
                # Stop any playback and clear selection
                try:
                    if self.vlc_player:
                        self._stop()
                except Exception:
                    pass
                try:
                    self.episode_list.clearSelection()
                    self.episode_list.setCurrentRow(-1)
                except Exception:
                    pass

                self.setEnabled(False)

                proc = QProcess(self)
                proc.setWorkingDirectory(os.path.dirname(exe_path) or os.getcwd())

                def on_finished(exit_code, exit_status):
                    try:
                        self.setEnabled(True)
                        self.status.showMessage(f"Game exited: {ep.title}", 5000)
                    finally:
                        # clear reference
                        try:
                            self._external_process = None
                        except Exception:
                            pass

                def on_error(err):
                    try:
                        self.setEnabled(True)
                        QMessageBox.critical(self, "Launch failed", f"Failed to start game (QProcess error): {err}")
                    finally:
                        try:
                            self._external_process = None
                        except Exception:
                            pass

                proc.finished.connect(on_finished)
                proc.errorOccurred.connect(on_error)
                # Keep a reference so it doesn't get GC'd
                self._external_process = proc
                proc.start(exe_path)
                if not proc.waitForStarted(2000):
                    # Could not start within 2s; treat as error but keep UI responsive
                    self.setEnabled(True)
                    QMessageBox.critical(self, "Launch failed", f"Failed to start game: {exe_path}")
                    self._external_process = None
                else:
                    self.status.showMessage(f"Launched game: {ep.title}", 5000)
            except Exception as e:
                try:
                    self.setEnabled(True)
                except Exception:
                    pass
                QMessageBox.critical(self, "Launch failed", f"Failed to start game.\n{e}")
            return

        media_path = ep.resolved_path()
        if not os.path.exists(media_path):
            QMessageBox.critical(self, "Missing file", f"Cannot find embedded video: {media_path}")
            return
        # Rebind the video surface handle in case it changed (winId can change on Windows)
        try:
            win_id = int(self.video_surface.winId())
            self.vlc_player.set_hwnd(win_id)
        except Exception:
            pass

        media = self.vlc_instance.media_new(os.path.abspath(media_path))
        sub_path = ep.resolved_subtitle()
        if self.subtitle_enabled and sub_path and os.path.exists(sub_path):
            # Use absolute path and VLC option prefix ':' to ensure libvlc sees it
            try:
                media.add_option(f":sub-file={os.path.abspath(sub_path)}")
            except Exception:
                # best-effort; continue without crashing
                pass

        self.vlc_player.set_media(media)
        r = self.vlc_player.play()
        if r == -1:
            QMessageBox.critical(self, "Playback error", "Failed to start playback.")
            return

        self.current_episode = ep
        # Hide the preview area while playback is active (user requested no thumbnail under video)
        try:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setVisible(False)
        except Exception:
            pass

        # If subtitles should be enabled, try to enable the first subtitle track shortly after playback starts
        sub_path = ep.resolved_subtitle()
        if self.subtitle_enabled and sub_path and os.path.exists(sub_path):
            try:
                # Schedule enabling subtitles after a short delay to let libVLC parse tracks
                QTimer.singleShot(500, lambda: self.vlc_player.video_set_spu(0))
            except Exception:
                pass
        # Hide selection panels when a new video starts
        try:
            self._set_panels_visible(False)
        except Exception:
            pass

        self.ui_timer.start()
        self.status.showMessage(f"Playing: {ep.title}", 3000)

    def _pause(self):
        self.vlc_player.pause()

    def _stop(self):
        self.vlc_player.stop()
        self.ui_timer.stop()
        self.position_slider.setValue(0)
        # Restore preview area when playback stops
        try:
            self.preview_label.setVisible(True)
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Episode Preview")
        except Exception:
            pass
        # Deselect current episode and show panels
        try:
            self.current_episode = None
            self.episode_list.clearSelection()
            self.episode_list.setCurrentRow(-1)
            self._set_panels_visible(True)
        except Exception:
            pass

    def _on_slider_moved(self, pos_ms: int):
        # If slider range equals media length, this value is milliseconds
        try:
            self.vlc_player.set_time(pos_ms)
        except Exception:
            pass
        # Update displayed time while dragging/choosing
        try:
            total = self.vlc_player.get_length()
            cur = pos_ms
            self.position_label.setText(f"{self._format_ms(cur)} / {self._format_ms(total)}")
        except Exception:
            pass

    def _on_volume_changed(self, value: int):
        self.vlc_player.audio_set_volume(value)

    def _toggle_subtitles(self, checked: bool):
        self.subtitle_enabled = checked
        try:
            if checked:
                # If a media is currently loaded/playing, try to (re)load it with the subtitle file
                try:
                    sub_path = None
                    if self.current_episode:
                        sub_path = self.current_episode.resolved_subtitle()
                    # If subtitles exist for the current episode, reload media with the ':sub-file' option
                    if sub_path and os.path.exists(sub_path) and self.current_episode:
                        # preserve current time and state
                        try:
                            cur_time = self.vlc_player.get_time()
                        except Exception:
                            cur_time = 0
                        try:
                            was_playing = bool(self.vlc_player.is_playing())
                        except Exception:
                            was_playing = False
                        # Mute and hide video surface to avoid visible jump to start
                        try:
                            was_muted = bool(self.vlc_player.audio_get_mute())
                        except Exception:
                            was_muted = False
                        try:
                            self.vlc_player.audio_set_mute(True)
                        except Exception:
                            pass
                        try:
                            self.video_surface.setVisible(False)
                        except Exception:
                            pass

                        try:
                            media = self.vlc_instance.media_new(os.path.abspath(self.current_episode.resolved_path()))
                            media.add_option(f":sub-file={os.path.abspath(sub_path)}")
                            self.vlc_player.set_media(media)
                            r = self.vlc_player.play()
                            # Restore time/state after a short delay; video surface remains hidden until restored
                            def _restore_after_reload():
                                try:
                                    if cur_time and cur_time > 0:
                                        try:
                                            self.vlc_player.set_time(cur_time)
                                        except Exception:
                                            pass
                                    # restore playing/paused state
                                    try:
                                        if not was_playing:
                                            # pause to return to previous paused state
                                            self.vlc_player.pause()
                                    except Exception:
                                        pass
                                    # enable first subtitle track
                                    try:
                                        self.vlc_player.video_set_spu(0)
                                    except Exception:
                                        pass
                                finally:
                                    # unmute and show video surface
                                    try:
                                        self.vlc_player.audio_set_mute(was_muted)
                                    except Exception:
                                        pass
                                    try:
                                        self.video_surface.setVisible(True)
                                    except Exception:
                                        pass

                            QTimer.singleShot(400, _restore_after_reload)
                        except Exception:
                            # Fallback: try to enable subtitle track directly
                            try:
                                self.vlc_player.video_set_spu(0)
                            except Exception:
                                pass
                    else:
                        # No current media or subtitle file not found — just attempt to enable SPU
                        try:
                            self.vlc_player.video_set_spu(0)
                        except Exception:
                            pass
                except Exception:
                    # final fallback: try to enable SPU directly
                    try:
                        self.vlc_player.video_set_spu(0)
                    except Exception:
                        pass
            else:
                # Disable subtitles
                try:
                    self.vlc_player.video_set_spu(-1)
                except Exception:
                    pass
        except Exception:
            # Fallback message even if no track is available yet
            pass
        self.status.showMessage(f"Subtitles {'enabled' if checked else 'disabled'}", 3000)

    def _toggle_fullscreen(self, checked: bool):
        # First try letting VLC toggle fullscreen for the video output
        try:
            self.vlc_player.set_fullscreen(checked)
        except Exception:
            pass
        # Fallback: toggle the Qt window fullscreen state so the user sees fullscreen
        try:
            if checked:
                self.showFullScreen()
            else:
                self.showNormal()
        except Exception:
            pass

    def _sync_ui(self):
        length = self.vlc_player.get_length()
        if length and length > 0:
            if self.position_slider.maximum() != length:
                self.position_slider.setRange(0, length)

        pos = self.vlc_player.get_time()
        if pos is not None and pos >= 0:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(pos)
            self.position_slider.blockSignals(False)
            # Update time label
            try:
                self.position_label.setText(f"{self._format_ms(pos)} / {self._format_ms(length)}")
            except Exception:
                pass

    def _about(self):
        QMessageBox.information(
            self,
            "About",
            "DVD-Style Menu Player\n"
            "• PySide6 UI\n"
            "• VLC backend via python-vlc\n"
            "• Episode thumbnails and season logos via manifest\n"
            "• Subtitles toggle and Fullscreen playback"
        )

    def _on_media_ended(self):
        # Called when VLC reports end of media
        try:
            self.ui_timer.stop()
        except Exception:
            pass
        try:
            # Deselect and reveal panels
            self.current_episode = None
            self.episode_list.clearSelection()
            self.episode_list.setCurrentRow(-1)
            self._set_panels_visible(True)
            self.status.showMessage("Playback finished", 3000)
        except Exception:
            pass


def load_manifest(manifest_path: str):
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        seasons = []
        for season_entry in data.get("seasons", []):
            title = season_entry.get("title", "Untitled Season")
            logo = season_entry.get("logo")
            episodes = []
            for ep in season_entry.get("episodes", []):
                episodes.append(
                    Episode(
                        title=ep.get("title", "Untitled Episode"),
                        path=ep["path"],
                        is_external_exe=ep.get("external_exe", False),
                        subtitle_path=ep.get("subtitle"),
                        thumbnail_path=ep.get("thumbnail"),
                    )
                )
            seasons.append(Season(title, episodes, logo_path=logo))
        return seasons


def main():
    app = QApplication(sys.argv)

    manifest_file = resource_path("manifest.json")
    if not os.path.exists(manifest_file):
        QMessageBox.critical(None, "Manifest missing", f"Cannot find manifest.json at {manifest_file}")
        sys.exit(1)

    seasons = load_manifest(manifest_file)
    window = DvdStylePlayer(seasons)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
