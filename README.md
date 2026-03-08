# Media Player

A DVD-style media player application for browsing and playing videos, music, executables and presenting images.

## Features

- **DVD-Style Interface**: Navigate through seasons and episodes with thumbnail previews
- **Multi-Media Support**: Play videos, audio tracks, view image galleries, and launch external applications
- **Subtitle Support**: Toggle subtitles on/off for supported content
- **Fullscreen Mode**: Immersive viewing experience
- **Customizable UI**: Hide side panels for more screen space
- **VLC Integration**: Powered by VLC media player for robust playback

## Requirements

- Python 3.8+
- PySide6 (Qt for Python)
- python-vlc
- VLC Media Player installed on the system

## Installation

1. Ensure Python 3.8 or higher is installed
2. Install required Python packages:
   ```bash
   pip install PySide6 python-vlc
   ```
3. Install VLC Media Player from https://www.videolan.org/vlc/

## Usage

1. Place your media files in the appropriate directories (see manifest.json structure)
2. Run the application:
   ```bash
   python app.py
   ```
3. Use the season list on the left to select a season
4. Select an episode from the episode list
5. Click "Start" or double-click an episode to begin playback

## Controls

- **Play/Pause**: Control media playback
- **Stop**: Stop current playback
- **Seek**: Drag the position slider to jump to different parts of the media
- **Volume**: Adjust playback volume
- **Subtitles**: Toggle subtitle display
- **Fullscreen**: Toggle fullscreen mode (F11 or menu)
- **Panels**: Hide/show side panels for more viewing space

## Keyboard Shortcuts

- **Space**: Play/Pause
- **F11**: Toggle fullscreen
- **Escape**: Exit fullscreen
- **Arrow Keys**: Seek backward/forward
- **S**: Toggle subtitles

## Configuration

The application uses a `manifest.json` file to define the media structure. Each season contains episodes with metadata like titles, file paths, thumbnails, and subtitles.

## Troubleshooting

- **VLC not found**: Ensure VLC is installed and python-vlc is properly installed
- **Media not playing**: Check file paths in manifest.json and ensure files exist
- **Subtitles not showing**: Verify subtitle file paths and format compatibility

## License

This project is open source. See individual files for license information.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.
