#!/usr/bin/env python3
# encoding: utf-8

import argparse
import sys
from dasbus.loop import EventLoop
from dasbus.connection import SessionMessageBus
import os
from tonmeister import Tonmeister

def main():
    parser = argparse.ArgumentParser(description="Record tracks played by spotify via pulseaudio.")
    parser.add_argument('--dir',
                        help="Directory for storing files. "
                        "(Default: current directory)",
                        default="",
                        type=str)
    parser.add_argument('--name',
                        help="File name pattern for recordings. "
                        "(Default: '@artist - @album - @trackNumber - @title'",
                        default="@artist - @album - @trackNumber - @title",
                        type=str)
    parser.add_argument('--sink',
                        help="Pulseaudio sink to record from.",
                        type=str,
                        required=True)
    parser.add_argument('--delay',
                        help="Seconds to wait after switching tracs before starting to record. "
                        "(Default: 2.0 seconds)",
                        default=2.0,
                        type=float)
    parser.add_argument('--command',
                        help="Command to start for recording. "
                        "@sink specifies Pulseaudio source sink. "
                        "@length specifies the recording length in seconds. "
                        "@file specifies the output file. "
                        "(Default: 'ffmpeg -y -hide_banner -loglevel error -ss @delay -f pulse -ac 2 -ar 44100 -i @sink -c:a libmp3lame -qscale:a 3 -filter:a silenceremove=start_periods=1:start_duration=1:start_threshold=-60dB:detection=peak -t @length @file')",
                        default="ffmpeg -y -hide_banner -loglevel error -ss @delay -f pulse -ac 2 -ar 44100 -i @sink -c:a libmp3lame -qscale:a 3 -filter:a silenceremove=start_periods=1:start_duration=1:start_threshold=0:detection=peak -t @length @file",
                        type=str)
    parser.add_argument('--useragent',
                        help="User-Agent for HTTP Requests downloading cover artwork. "
                        "(Default: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'",
                        default='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
                        type=str)
    args = parser.parse_args()
    tonmeister = Tonmeister(**vars(args))
    if args.dir:
        if not os.path.exists(args.dir):
            create_dir = input("Directory doesn't exist. Create? [y/n] ")
            if create_dir == 'y':
                os.makedirs(args.dir, exist_ok=True)
            else:
                sys.exit(1)
        tonmeister.rec_dir = os.path.abspath(args.dir)
    if args.sink:
        tonmeister.pulseaudio_sink = args.sink
        print(f"Recording from explicitly set sink {tonmeister.pulseaudio_sink}.")
    else:
        raise NotImplementedError("Must specify sink.")
    
    bus = SessionMessageBus()
    try:
        proxy = bus.get_proxy("org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2")
        proxy.PropertiesChanged.connect(tonmeister.on_properties_changed)
    except :
        sys.exit(1)
    loop = EventLoop()

    try:
        print("Start recording on next track.")
        loop.run()
    except KeyboardInterrupt:
        print("Received KeyboardInterrupt. Quitting...")
        tonmeister.stop_all()

if __name__ == '__main__':
    main()
