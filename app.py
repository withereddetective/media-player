import json
import os
import sys
import subprocess

from collections import OrderedDict
from PySide6.QtCore import Qt, QSize, QTimer, QProcess, QEvent
from PySide6.QtGui import QAction, QIcon, QPixmap, QImageReader
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QListWidget, QListWidgetItem, QListView,
    QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QSplitter, QMessageBox, QStyle, QSlider, QStatusBar, QWidget, QSizePolicy
)

import vlc



def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        # Running from a PyInstaller bundle
        return os.path.join(sys._MEIPASS, relative_path)
    else:
        # Running in normal Python
        return os.path.join(os.path.abspath("."), relative_path)


class Episode:
    def __init__(self, title, path, is_external_exe=False,
                 subtitle_path=None, thumbnail_path=None, images=None, base_dir=None):
        self.title = title
        self.path = path
        self.is_external_exe = is_external_exe
        self.subtitle_path = subtitle_path
        self.thumbnail_path = thumbnail_path
        self.images = images or []
        self.base_dir = base_dir or os.getcwd()
        self.length_str = None

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

    def is_audio(self):
        if not self.path:
            return False
        ext = os.path.splitext(self.path)[1].lower()
        return ext in ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.m4b', '.aiff', '.au', '.webm']

    def is_image(self):
        return bool(self.images)

    def resolved_images(self):
        return [os.path.join(self.base_dir, img) for img in self.images]

    def get_length_str(self):
        if self.length_str is not None:
            return self.length_str
        if self.is_image():
            self.length_str = f"{len(self.images)} images"
            return self.length_str
        if self.path and os.path.exists(self.resolved_path()):
            try:
                instance = vlc.Instance('--no-video', '--no-audio')  # Minimal instance for metadata only
                media = instance.media_new(self.resolved_path())
                media.parse_with_options(vlc.MediaParseFlag.local, 5000)  # Timeout 5s
                duration = media.get_duration()
                if duration > 0:
                    s = int(duration // 1000)
                    h = s // 3600
                    m = (s % 3600) // 60
                    sec = s % 60
                    if h:
                        self.length_str = f"{h}:{m:02d}:{sec:02d}"
                    else:
                        self.length_str = f"{m:02d}:{sec:02d}"
                else:
                    self.length_str = "Unknown"
            except Exception as e:
                print(f"Error getting duration for {self.path}: {e}")
                self.length_str = "Unknown"
        else:
            self.length_str = ""
        return self.length_str


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
        self.video_surface.installEventFilter(self)

        self.preview_label = QLabel("Episode Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setMinimumHeight(150)

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
        # Track if video was playing during slider drag
        self.was_playing_during_drag = False
        # Current image index for image episodes
        self.current_image_index = 0
        # Original splitter sizes for panel restoration
        self._original_sizes = None

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
        self.play_btn = QPushButton()
        self.play_btn.setText("Start")
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.clicked.connect(self._play_selected)

        self.pause_btn = QPushButton()
        self.pause_btn.setText("Pause")
        self.pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.pause_btn.clicked.connect(self._toggle_play_pause)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_btn.clicked.connect(self._stop)

        self.launch_btn = QPushButton("Launch")
        self.launch_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.launch_btn.clicked.connect(self._launch_exe)
        self.launch_btn.setVisible(False)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        # Make the runtime slider larger (stretch) and update during moves
        self.position_slider.sliderMoved.connect(self._on_slider_moved)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)

        # Time label showing current / total
        self.position_label = QLabel("00:00 / 00:00")

        self.vol_label = QLabel("Vol")
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

        # Image navigation buttons
        self.back_btn = QPushButton("<")
        self.back_btn.setFixedWidth(40)
        self.back_btn.clicked.connect(self._prev_image)
        self.back_btn.setVisible(False)

        self.next_btn = QPushButton(">")
        self.next_btn.setFixedWidth(40)
        self.next_btn.clicked.connect(self._next_image)
        self.next_btn.setVisible(False)

        controls = QHBoxLayout()
        controls.addWidget(self.back_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.launch_btn)
        # Give runtime slider stretch so it becomes larger than the volume control
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.position_label)
        controls.addWidget(self.vol_label)
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.subtitle_button)
        controls.addWidget(self.next_btn)
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
        except Exception as e:
            print(f"Error loading pixmap for {path}: {e}")
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
            except Exception as e2:
                print(f"Fallback pixmap load failed for {path}: {e2}")
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
            episodes_count = sum(1 for ep in season.episodes if not ep.is_audio())
            tracks_count = sum(1 for ep in season.episodes if ep.is_audio())
            parts = []
            if episodes_count:
                parts.append(f"{episodes_count} episodes")
            if tracks_count:
                parts.append(f"{tracks_count} tracks")
            count_str = ", ".join(parts) if parts else ""
            label = season.title
            if count_str:
                label += "\n" + count_str
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, season)
            logo = season.resolved_logo()
            if logo and os.path.exists(logo):
                pixmap = self._load_scaled_pixmap(logo, self.ICON_WIDTH)
                if not pixmap.isNull():
                    icon = QIcon(pixmap)
                    item.setIcon(icon)
            # Ensure items have enough vertical space for icon + blank line + title
            item.setSizeHint(QSize(self.ICON_WIDTH + 20, self.ICON_HEIGHT + 65))
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
                if visible:
                    self.splitter.setVisible(visible)
                    self.splitter.update()
                else:
                    # Store current sizes before hiding
                    self._original_sizes = self.splitter.sizes()
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
                        if visible:
                            w.setVisible(visible)
                            w.update()
                        else:
                            self._original_sizes = w.sizes()
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
            label = ep.title + ("  [Game]" if ep.is_external_exe else ("  [Images]" if ep.is_image() else ""))
            if not ep.is_external_exe:
                length = ep.get_length_str()
                if length:
                    label += "\n" + length
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, ep)
            thumb = ep.resolved_thumbnail()
            if thumb and os.path.exists(thumb):
                pixmap = self._load_scaled_pixmap(thumb, self.ICON_WIDTH)
                if not pixmap.isNull():
                    icon = QIcon(pixmap)
                    item.setIcon(icon)
            item.setSizeHint(QSize(self.ICON_WIDTH + 20, self.ICON_HEIGHT + 65))
            self.episode_list.addItem(item)
        if self.episode_list.count() > 0:
            self.episode_list.setCurrentRow(0)

    def _on_episode_selected(self, current, previous):
        if not current:
            self.current_episode = None
            return
        ep = current.data(Qt.UserRole)
        self.current_episode = ep
        # Set up preview UI based on episode type
        if ep.is_external_exe:
            # For exe episodes, set up display like image episodes
            self.video_surface.setVisible(False)
            self.preview_label.setVisible(True)
            if ep.images:
                self.current_image_index = 0
                self._show_image(ep.resolved_images()[self.current_image_index])
                self.back_btn.setVisible(len(ep.images) > 1)
                self.next_btn.setVisible(len(ep.images) > 1)
                try:
                    self.back_btn.clicked.disconnect()
                    self.next_btn.clicked.disconnect()
                except:
                    pass
                self.back_btn.clicked.connect(self._prev_image)
                self.next_btn.clicked.connect(self._next_image)
            else:
                thumb = ep.resolved_thumbnail()
                if thumb and os.path.exists(thumb):
                    pixmap = self._load_scaled_pixmap(thumb, self.preview_label.width())
                    if not pixmap.isNull():
                        scaled_pixmap = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self.preview_label.setPixmap(scaled_pixmap)
                else:
                    self.preview_label.setPixmap(QPixmap())
                    self.preview_label.setText("Game Preview")
                self.back_btn.setVisible(False)
                self.next_btn.setVisible(False)
            self.position_slider.setVisible(False)
            self.position_label.setVisible(False)
            # Hide video controls, show launch button
            self.play_btn.setVisible(False)
            self.pause_btn.setVisible(False)
            self.stop_btn.setVisible(True)
            self.stop_btn.setText("Hide Panels" if self._panels_visible else "Show Panels")
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self._toggle_panels)
            self.launch_btn.setVisible(True)
            self.subtitle_button.setVisible(False)
            self.volume_slider.setVisible(False)
            self.vol_label.setVisible(False)
        elif ep.is_image():
            # For image episodes, set up the display
            self.current_image_index = 0
            self._show_image(ep.resolved_images()[self.current_image_index])
            self.video_surface.setVisible(False)
            self.preview_label.setVisible(True)
            if len(ep.images) > 1:
                self.back_btn.setVisible(True)
                self.next_btn.setVisible(True)
                try:
                    self.back_btn.clicked.disconnect()
                    self.next_btn.clicked.disconnect()
                except:
                    pass
                self.back_btn.clicked.connect(self._prev_image)
                self.next_btn.clicked.connect(self._next_image)
            else:
                self.back_btn.setVisible(False)
                self.next_btn.setVisible(False)
            self.position_slider.setVisible(False)
            self.position_label.setVisible(False)
            # Hide video controls for images
            self.play_btn.setVisible(False)
            self.pause_btn.setVisible(False)
            self.subtitle_button.setVisible(False)
            self.volume_slider.setVisible(False)
            self.vol_label.setVisible(False)
            self.launch_btn.setVisible(False)
            self.stop_btn.setVisible(True)
            self.stop_btn.setText("Hide Panels" if self._panels_visible else "Show Panels")
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self._toggle_panels)
        else:
            # For audio and video, show thumbnail in preview
            self.video_surface.setVisible(False)
            thumb = ep.resolved_thumbnail()
            if thumb and os.path.exists(thumb):
                pixmap = self._load_scaled_pixmap(thumb, self.preview_label.width())
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.preview_label.setPixmap(scaled_pixmap)
            else:
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText("Episode Preview")
            self.preview_label.setVisible(True)
            # Show media controls
            self.play_btn.setVisible(True)
            self.pause_btn.setVisible(True)
            self.stop_btn.setVisible(True)
            self.stop_btn.setText("Stop")
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self._stop)
            self.launch_btn.setVisible(False)
            self.subtitle_button.setVisible(True)
            self.volume_slider.setVisible(True)
            self.vol_label.setVisible(True)
            self.position_slider.setVisible(True)
            self.position_label.setVisible(True)
            self.back_btn.setVisible(False)
            self.next_btn.setVisible(False)

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
            # For exe episodes, set up display like image episodes
            self.current_episode = ep
            self.video_surface.setVisible(False)
            self.preview_label.setVisible(True)
            if ep.images:
                self.current_image_index = 0
                self._show_image(ep.resolved_images()[self.current_image_index])
                self.back_btn.setVisible(len(ep.images) > 1)
                self.next_btn.setVisible(len(ep.images) > 1)
                try:
                    self.back_btn.clicked.disconnect()
                    self.next_btn.clicked.disconnect()
                except:
                    pass
                self.back_btn.clicked.connect(self._prev_image)
                self.next_btn.clicked.connect(self._next_image)
            else:
                thumb = ep.resolved_thumbnail()
                if thumb and os.path.exists(thumb):
                    pixmap = self._load_scaled_pixmap(thumb, self.preview_label.width())
                    if not pixmap.isNull():
                        scaled_pixmap = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self.preview_label.setPixmap(scaled_pixmap)
                else:
                    self.preview_label.setPixmap(QPixmap())
                    self.preview_label.setText("Game Preview")
                self.back_btn.setVisible(False)
                self.next_btn.setVisible(False)
            self.position_slider.setVisible(False)
            self.position_label.setVisible(False)
            self._set_panels_visible(False)
            # Hide video controls, show launch button
            self.play_btn.setVisible(False)
            self.pause_btn.setVisible(False)
            self.stop_btn.setVisible(True)
            self.stop_btn.setText("Show Panels")
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self._toggle_panels)
            self.launch_btn.setVisible(True)
            self.subtitle_button.setVisible(False)
            self.volume_slider.setVisible(False)
            self.vol_label.setVisible(False)
            self.status.showMessage(f"Ready to launch: {ep.title}", 3000)
            return
        elif ep.is_image():
            # For image episodes, just set up the display
            self.current_episode = ep
            self.current_image_index = 0
            self._show_image(ep.resolved_images()[self.current_image_index])
            self.video_surface.setVisible(False)
            self.preview_label.setVisible(True)
            if len(ep.images) > 1:
                self.back_btn.setVisible(True)
                self.next_btn.setVisible(True)
                try:
                    self.back_btn.clicked.disconnect()
                    self.next_btn.clicked.disconnect()
                except:
                    pass
                self.back_btn.clicked.connect(self._prev_image)
                self.next_btn.clicked.connect(self._next_image)
            else:
                self.back_btn.setVisible(False)
                self.next_btn.setVisible(False)
            self.position_slider.setVisible(False)
            self.position_label.setVisible(False)
            self._set_panels_visible(False)
            # Hide video controls for images
            self.play_btn.setVisible(False)
            self.pause_btn.setVisible(False)
            self.subtitle_button.setVisible(False)
            self.volume_slider.setVisible(False)
            self.vol_label.setVisible(False)
            self.launch_btn.setVisible(False)
            self.stop_btn.setVisible(True)
            self.stop_btn.setText("Show Panels")
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self._toggle_panels)
            self.status.showMessage(f"Viewing: {ep.title}", 3000)
            return
        # For audio and video, proceed with VLC
        media_path = ep.resolved_path()
        if not os.path.exists(media_path):
            QMessageBox.critical(self, "Missing file", f"Cannot find embedded video: {media_path}")
            return
        # ... rest of the code
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
            except Exception as e:
                print(f"Warning: Failed to add subtitle option: {e}")

        self.vlc_player.set_media(media)
        r = self.vlc_player.play()
        if r == -1:
            QMessageBox.critical(self, "Playback error", f"Failed to start playback for {ep.title}.")
            return

        self.current_episode = ep
        # Handle audio vs video vs image display
        if ep.is_audio():
            # For audio, hide video surface and show thumbnail in preview
            self.video_surface.setVisible(False)
            thumb = ep.resolved_thumbnail()
            if thumb and os.path.exists(thumb):
                pixmap = self._load_scaled_pixmap(thumb, self.preview_label.width())
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.preview_label.setPixmap(scaled_pixmap)
            self.preview_label.setVisible(True)
            self.launch_btn.setVisible(False)
            # Ensure subtitles are disabled for audio
            self.vlc_player.video_set_spu(-1)
        else:
            # Hide the preview area while playback is active (user requested no thumbnail under video)
            try:
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setVisible(False)
                self.video_surface.setVisible(True)
            except Exception:
                pass
            self.launch_btn.setVisible(False)

        # Set common controls for media playback
        self.play_btn.setVisible(True)
        self.pause_btn.setVisible(True)
        self.stop_btn.setVisible(True)
        self.stop_btn.setText("Stop")
        self.stop_btn.clicked.disconnect()
        self.stop_btn.clicked.connect(self._stop)
        self.subtitle_button.setVisible(True)
        self.volume_slider.setVisible(True)
        self.vol_label.setVisible(True)
        self.position_slider.setVisible(True)
        self.position_label.setVisible(True)
        self.back_btn.setVisible(False)
        self.next_btn.setVisible(False)

        # If subtitles should be enabled, try to enable the first subtitle track shortly after playback starts
        sub_path = ep.resolved_subtitle()
        has_subs = sub_path and os.path.exists(sub_path)
        if self.subtitle_enabled and has_subs:
            try:
                # Schedule enabling subtitles after a short delay to let libVLC parse tracks
                QTimer.singleShot(500, lambda: self.vlc_player.video_set_spu(0))
            except Exception:
                pass
        else:
            # Ensure subtitles are disabled if no subtitle file or not enabled
            try:
                self.vlc_player.video_set_spu(-1)
            except Exception:
                pass
        # Hide selection panels when a new video starts
        try:
            self._set_panels_visible(False)
        except Exception:
            pass

        self.ui_timer.start()
        self.status.showMessage(f"Playing: {ep.title}", 3000)

    def _launch_exe(self):
        if not self.current_episode or not self.current_episode.is_external_exe:
            return
        exe_path = self.current_episode.resolved_path()
        if not os.path.exists(exe_path):
            QMessageBox.critical(self, "Missing game", f"Cannot find embedded game: {exe_path}")
            return
        # Store episode info before stopping
        ep = self.current_episode
        # Stop any current playback
        try:
            if self.vlc_player:
                self._stop()
        except Exception:
            pass

        self.setEnabled(False)
        self.launch_btn.setEnabled(False)

        proc = QProcess(self)
        proc.setWorkingDirectory(os.path.dirname(exe_path) or os.getcwd())

        def on_finished(exit_code, exit_status):
            try:
                self.setEnabled(True)
                self.launch_btn.setEnabled(True)
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
                self.launch_btn.setEnabled(True)
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
            self.launch_btn.setEnabled(True)
            QMessageBox.critical(self, "Launch failed", f"Failed to start game: {exe_path}")
            self._external_process = None
        else:
            self.status.showMessage(f"Launched game: {ep.title}", 5000)

    def _toggle_panels(self):
        visible = not self._panels_visible
        self._set_panels_visible(visible)
        self.stop_btn.setText("Hide Panels" if visible else "Show Panels")
        # Reload image size if currently viewing an image or exe episode
        if self.current_episode and (self.current_episode.is_image() or self.current_episode.is_external_exe) and self.current_episode.images:
            self._show_image(self.current_episode.resolved_images()[self.current_image_index])

    def _toggle_play_pause(self):
        try:
            if self.vlc_player.is_playing():
                self.vlc_player.pause()
            else:
                self.vlc_player.play()
        except Exception:
            pass

    def _stop(self):
        self.vlc_player.stop()
        self.ui_timer.stop()
        self.position_slider.setValue(0)
        # Restore UI: show video surface, reset preview, hide image buttons
        try:
            self.video_surface.setVisible(True)
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Episode Preview")
            self.preview_label.setVisible(True)
            self.back_btn.setVisible(False)
            self.next_btn.setVisible(False)
            self.position_slider.setVisible(True)
            self.position_label.setVisible(True)
            self.vol_label.setVisible(True)
            self.play_btn.setVisible(True)
            self.pause_btn.setVisible(True)
            self.stop_btn.setVisible(True)
            self.stop_btn.setText("Stop")
            self.launch_btn.setVisible(False)
            self.subtitle_button.setVisible(True)
            self.volume_slider.setVisible(True)
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

    def _on_slider_pressed(self):
        try:
            self.was_playing_during_drag = bool(self.vlc_player.is_playing())
            if self.was_playing_during_drag:
                self.vlc_player.pause()
        except Exception:
            pass

    def _on_slider_released(self):
        try:
            if self.was_playing_during_drag:
                self.vlc_player.play()
            self.was_playing_during_drag = False
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj == self.video_surface and event.type() == QEvent.MouseButtonPress:
            self._toggle_play_pause()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._toggle_play_pause()
        elif event.key() == Qt.Key_Left:
            self._seek_backward(10)
        elif event.key() == Qt.Key_Right:
            self._seek_forward(10)
        elif event.key() == Qt.Key_Comma:
            self._seek_backward_frame()
        elif event.key() == Qt.Key_Period:
            self._seek_forward_frame()
        elif event.key() == Qt.Key_F:
            self._toggle_fullscreen()
        elif event.key() == Qt.Key_M:
            self._toggle_mute()
        else:
            super().keyPressEvent(event)

    def _on_volume_changed(self, value: int):
        self.vlc_player.audio_set_volume(value)

    def _seek_backward(self, seconds: int):
        try:
            current = self.vlc_player.get_time()
            new_time = max(0, current - seconds * 1000)
            self.vlc_player.set_time(new_time)
        except Exception:
            pass

    def _seek_forward(self, seconds: int):
        try:
            current = self.vlc_player.get_time()
            length = self.vlc_player.get_length()
            new_time = min(length, current + seconds * 1000)
            self.vlc_player.set_time(new_time)
        except Exception:
            pass

    def _seek_backward_frame(self):
        try:
            self.vlc_player.previous_frame()
        except Exception:
            pass

    def _seek_forward_frame(self):
        try:
            self.vlc_player.next_frame()
        except Exception:
            pass

    def _toggle_mute(self):
        try:
            current_mute = self.vlc_player.audio_get_mute()
            self.vlc_player.audio_set_mute(not current_mute)
        except Exception:
            pass

    def _show_image(self, image_path):
        if os.path.exists(image_path):
            pixmap = self._load_scaled_pixmap(image_path, self.preview_label.width())
            if not pixmap.isNull():
                if self.current_episode and self.current_episode.is_external_exe:
                    if not self._panels_visible:
                        # Panels hidden, fit screen
                        scaled_pixmap = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    else:
                        # Panels shown, 600x600
                        scaled_pixmap = pixmap.scaled(QSize(600, 600), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                else:
                    scaled_pixmap = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_label.setPixmap(scaled_pixmap)
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Image not found")

    def _prev_image(self):
        if self.current_episode and (self.current_episode.is_image() or self.current_episode.is_external_exe) and self.current_episode.images:
            self.current_image_index = (self.current_image_index - 1) % len(self.current_episode.images)
            self._show_image(self.current_episode.resolved_images()[self.current_image_index])

    def _next_image(self):
        if self.current_episode and (self.current_episode.is_image() or self.current_episode.is_external_exe) and self.current_episode.images:
            self.current_image_index = (self.current_image_index + 1) % len(self.current_episode.images)
            self._show_image(self.current_episode.resolved_images()[self.current_image_index])

    def _toggle_subtitles(self, checked: bool):
        if not self.subtitle_button.isEnabled():
            return
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

    def _toggle_fullscreen(self):
        checked = not self.isFullScreen()
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
        # Update menu action state
        try:
            self.fullscreen_action.setChecked(checked)
        except Exception:
            pass

    def _sync_ui(self):
        try:
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
                except Exception as e:
                    print(f"Error updating time label: {e}")
            # Update pause button text
            try:
                if self.vlc_player.is_playing():
                    self.pause_btn.setText("Pause")
                else:
                    self.pause_btn.setText("Resume")
            except Exception:
                pass
        except Exception as e:
            print(f"Error in _sync_ui: {e}")

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
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        QMessageBox.critical(None, "Manifest missing", f"Cannot find manifest.json at {manifest_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        QMessageBox.critical(None, "Manifest error", f"Invalid JSON in manifest.json: {e}")
        sys.exit(1)
    except Exception as e:
        QMessageBox.critical(None, "Manifest error", f"Error loading manifest: {e}")
        sys.exit(1)

    base_dir = os.path.dirname(manifest_path) or os.getcwd()

    seasons = []
    for season_entry in data.get("seasons", []):
        title = season_entry.get("title", "Untitled Season")
        logo = season_entry.get("logo")
        episodes = []
        for ep in season_entry.get("episodes", []):
            images = ep.get("images", [])
            thumbnail_path = ep.get("thumbnail")
            if not thumbnail_path and images:
                thumbnail_path = images[0]
            episodes.append(
                Episode(
                    title=ep.get("title", "Untitled Episode"),
                    path=ep.get("path"),
                    is_external_exe=ep.get("external_exe", False),
                    subtitle_path=ep.get("subtitle"),
                    thumbnail_path=thumbnail_path,
                    images=images,
                    base_dir=base_dir,
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
