## *drainify* - Record your spotify stream from pulseaudio.

### Changes
This version differs from "coderiot" upstream:

* ffmpeg is used for capturing and compression.
* Any existing pulseaudio source can be chosen to record from.
* Output file name pattern can be configured.

### Dependencies
 * pulseaudio
 * ffmpeg
 * native spotify-client
 * optional: pulseaudio-utils (command line tools)

### Requirements
 * dbus-python (does not support distutils)
 * eyed3d
 * pygobject (does not support distutils)

Notice: You have to install dbus-python and pyobject by your own

### Install dependecies on Ubuntu
```sh
sudo apt-get install pulseaudio-utils dbus-python python-gobject python-eyed3 ffmpeg
```

### Installation of drainify

```sh
$ pip install -e git+https://github.com/hoehermann/drainify.git#egg=drainify
```

### Features
 * split stream into audio files
 * does not record ads
 * id3tags and album art for audio files

### spotify preferences
 * disable gapless playing
 * disable crossfade tracks

### usage
Start spotify before recording.

Call:
```sh
drainify [--dir <MUSIC_DIR>] [--sink <PULSEAUDIO_SOURCE_NAME>] [--name <OUTPUT_FILENAME_PATTERN>]
```

Example:
```sh
drainify --dir ~/spotify-recordings --sink alsa_output.pci-0000_01_01.0.analog-stereo.monitor
--name "@artist - @album - @trackNumber - @title"
```
