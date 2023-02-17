#!/usr/bin/env python3
# encoding: utf-8
from __future__ import print_function

import argparse
import datetime
import os
import time
import re
import signal
import subprocess
import sys
import tempfile as tmp
from urllib.request import urlopen
from urllib.error import HTTPError

import dbus
import dbus.mainloop.glib
import eyed3
import threading
from gi.repository import GLib

import pa

# url prefix for album covers
IMG_PREFIX = "https://d3rt1990lpmkn.cloudfront.net/320/"

config = type('Config', (object,), {
'rec_dir': os.getcwd(), 
'pa_sink': "combined.monitor"
})()

# running recorders
running_recs = []

def set_id3_tags(filename, metadata):
    """Set the ID3 tags for an audio file.

    :filename: location of the audio file
    :metadata: contains information for the audio file from the dbus event

    """
    try:
        audiofile = eyed3.load(filename)
    except OSError as ose:
        # this can happen when running recording is aborted
        if (ose.errno == 2):
            print("Unable to tag file \"%s\". Does not exist."%(filename))
            return
        else:
            raise
    except IOError as ioe:
        # this can happen when running recording is aborted
        print("Unable to tag file \"%s\" due to IOError."%(filename))
        return
    audiofile.initTag()

    audiofile.tag.artist = metadata['xesam:artist'][0]
    audiofile.tag.album = metadata['xesam:album']
    audiofile.tag.title = metadata['xesam:title']
    audiofile.tag.track_num = int(metadata['xesam:trackNumber'])
    audiofile.tag.disc_num = int(metadata['xesam:discNumber'])

    try:
        cover_art_url = metadata['mpris:artUrl']
        _, cover_id = cover_art_url.rsplit('/', 1)
        art_url = IMG_PREFIX + cover_id
        image_data = urlopen(art_url).read()
        audiofile.tag.images.set(3, image_data, "image/jpeg", u"")
    except HTTPError as httperr:
        print("Unable to download cover art due to %s."%(str(httperr)))
    except ValueError as ve:
        # this can happen upon _, cover_id = cover_art_url.rsplit('/', 1)
        pass
    #except URLError as ue: # TODO: fix import
    #    # this can happen upon image_data = urlopen(art_url).read()
    #    pass

    try:
        audiofile.tag.save()
    except UnicodeEncodeError as uee:
        print("Writing tags failed due to UnicodeEncodeError.")
        # this happens in Python 2 with non-ascii artist names like "Röyskopp"
        pass


class Recorder(object):
    """Record spotify stream via pulse audio."""
    
    def __init__(self, metadata, running_recs):
        """ Prepare recorder.
        :metadata: track information from dbus event
        :running_recs: reference to all running recorders
        """
        self.metadata = metadata
        self.running_recs = running_recs
        self.ffmpeg = None

        self.name = self.format_name(config.name_format)
        name = self.name
        # replace reserved characters
        for o,r in {
            u"/":u"／", # slash is directory separator on unixoid systems
            u"\\":u"＼", # backslash is directory separator on Windows systems. exFAT does not like \
            u"*":u"＊", # exFAT does not like *
            u"?":u"﹖", # FAT32 does not like ?
            u"<":u"‹", # FAT32 does not like >
            u">":u"›", # FAT32 does not like <
            u":":u"：" # FAT32 does not like :
        }.items():
            name = name.replace(o, r)
        self.final_path = os.path.join(config.rec_dir,"%s.mp3"%(name))
        
        m = self.metadata['Metadata']
        length = m["mpris:length"] # is in microseconds
        secs = length * 1E-6
        # avoid recording the beginning of the next track
        secs -= 0.750
        self.length_seconds = secs


    def format_name(self, name_format):
        """ Prepare file name based on format and metadata. """
        name = name_format
        m = self.metadata['Metadata']
        for arg in re.finditer("@[a-z]+", name_format, re.I):
            a = arg.group()
            s = m['xesam:%s'%(a.strip("@"))]
            s = s[0] if s.__class__ == dbus.Array else s
            name = name.replace(a, str(s))
        return name

    def is_advert(self):
        """ Whether this is an advertisement is being recorded. """
        m = self.metadata['Metadata']
        artist = m['xesam:artist'][0]
        return artist == ""


    def start(self):
        """ Start recording. """
        if (self.length_seconds < 1):
            print("Reported length is too short. Not starting to record.")
        elif (self.name in [r.name for r in self.running_recs]):
            # this is neccessary since the "this song is being played now"
            # message is sometimes received more than once for reasons unknown
            print("\"%s\" is already being recorded right now. "
            "Not starting to record again."%(self.name))
        elif (os.path.isfile(self.final_path)):
            print("\"%s\" already exists. Not overwriting."%(self.final_path))
        else:
            # TODO: make cmd-string configurable
            cmd = config.command.split()
            cmd = [c.replace('@length',str(self.length_seconds)).replace('@sink',config.pa_sink).replace('@file',self.final_path) for c in cmd]
            print("Starting: "+" ".join(cmd))
            self.ffmpeg = subprocess.Popen(cmd,preexec_fn=os.setsid)
            self.running_recs.append(self)
            # wait in background for the recording to finish
            t = threading.Thread(target=self.wait_recording)
            t.start()


    def stop_handler(self, keep_file = False):
        """ Request to stop recording. """
        # I am not sure when exactly this is fired
        try:
            self.running_recs.remove(self)
        except ValueError:
            # poor thead-safety: might already been removed
            pass
        # get record status
        rc = self.ffmpeg.poll() if self.ffmpeg else None
        if (rc is None):
            # recording is still running, ask it to terminate
            os.kill(self.ffmpeg.pid, signal.SIGTERM)
            self.ffmpeg.wait() # wait for it
            keep_file = False
            print("Aborted recording \"%s\"."%(self.name))
        if (self.is_advert()):
            keep_file = False
            print("%s is an advert."%(self.name))
        if (not keep_file):
            print("Removing \"%s\"..."%(self.final_path))
            try:
                os.remove(self.final_path)
            except OSError as ose:
                if (ose.errno == 2):
                    print("Unable to remove file \"%s\". Does not exist."%(self.final_path))
                else:
                    raise
        return keep_file

    def wait_recording(self):
        """ Wait for the recording to end. """
        # waiting for encoder to encode the end of stream
        self.ffmpeg.wait()
        print("Finished recording \"%s\"." % (self.name))
        kept_file = self.stop_handler(keep_file=True)
        if (kept_file):
            set_id3_tags(self.final_path, self.metadata['Metadata'])


def recording_handler(sender=None, metadata=None, sig=None):
    if "PlaybackStatus" in metadata:
        if metadata['PlaybackStatus'] == 'Paused':
            stop_all()
            return
        elif metadata['PlaybackStatus'] == 'Stopped':
            stop_all()
            return
        else:
            # assume metadata['PlaybackStatus'] == 'Playing'
            # this may try to start to record multiple times in parallel,
            # but Recorder.start() will check for that
            pass
    
    if 'Metadata' not in metadata:
        print("No Metadata. Restart Spotify, drainify, dbus and/or your user session.")
        return

    rec = Recorder(metadata, running_recs)
    if (running_recs):
        # this is not the first song being recorded
        # TODO: this does not work if recording finishes early
        sleeptime = config.delay # good in 2023 for "crossfade" and "automix" disabled TODO: make configurable
        try:
            if (running_recs[-1].is_advert()):
                # after an ad, sleep some more
                sleeptime += 1.0 # 2022
        except IndexError:
            # poor thread-safety: this happens if a recording finished between if (running_recs) and access to running_recs[-1]
            pass
        # do not record the end of the previous song, so sleep
        time.sleep(sleeptime)
    rec.start()


def debug_handler(sender=None, metadata=None, k2=None):
    print(datetime.datetime.now(), "got signal from ", sender)
    print(metadata.keys())
    print(k2)
    print("")


def stop_all():
    """Terminate all running recordings."""
    while running_recs:
        running_recs.pop().stop_handler()
    print("Stopped all recordings.")


def main():
    parser = argparse.ArgumentParser("Record tracks played by spotify via pulseaudio.")

    parser.add_argument('--dir',
                        '-d',
                        help="Directory for storing files. "
                        "(Default: current directory)",
                        type=str)

    parser.add_argument('--name',
                        '-n',
                        help="File name pattern for recordings. "
                        "(Default: @artist - @album - @trackNumber - @title",
                        default="@artist - @album - @trackNumber - @title",
                        type=str)

    parser.add_argument('--sink',
                        '-s',
                        help="Pulseaudio sink to record from. "
                        "(Default: search for spotify in particular)",
                        type=str)

    parser.add_argument('--delay',
                        '-r',
                        help="Seconds to wait after switching tracs before starting to record. "
                        "(Default: 2.5 seconds)",
                        default=2.5,
                        type=float)
                        
    parser.add_argument('--command',
                        '-c',
                        help="Command to start for recording. "
                        "@sink specifies Pulseaudio source sink. "
                        "@length specifies the recording length in seconds. "
                        "@file specifies the output file. "
                        "(Default: \"ffmpeg -hide_banner -loglevel error -f pulse -ac 2 -ar 44100 -i @sink -c:a libmp3lame -qscale:a 3 -y -t @length @file\")",
                        default="ffmpeg -hide_banner -loglevel error -f pulse -ac 2 -ar 44100 -i @sink -c:a libmp3lame -qscale:a 3 -y -t @length @file",
                        type=str)

    args = parser.parse_args()
    if args.dir:
        if not os.path.exists(args.dir):
            create_dir = input("Directory doesn't exist. Create? [y/n] ")
            if create_dir == 'y':
                os.mkdir(args.dir)
            else:
                sys.exit()
        config.rec_dir = os.path.abspath(args.dir)
        
    config.name_format = args.name
    config.command = args.command
    
    if args.sink:
        config.pa_sink = args.sink
        print("Recording from explicitly set sink %s."%(config.pa_sink))
    else:
        print("Looking for direct connection to spotify audio stream...")
        # init combined sink
        sinks = pa.list_sinks()
        sink_choose = None
        if len(sinks) > 1:
            for i, s in enumerate(sinks):
                print("%i: %s" % (i, s))
            sink_choose = input("Choose your audio device (Default [0]): ")
        # default sink
        if not sink_choose:
            sink_choose = 0
        rec_sink = sinks[sink_choose]
        spot_id = pa.find_spotify_input_sink()
        combined_sink = pa.create_combined_sink(rec_sink)
        pa.move_sink_input(spot_id)

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    try:
        remote_object = bus.get_object(
            "org.mpris.MediaPlayer2.spotify",
            "/org/mpris/MediaPlayer2"
        )
        change_manager = dbus.Interface(
            remote_object,
            'org.freedesktop.DBus.Properties'
        )
        change_manager.connect_to_signal(
            "PropertiesChanged",
            recording_handler
        )
    except dbus.exceptions.DBusException as dbe:
        if (dbe.get_dbus_name() == "org.freedesktop.DBus.Error.ServiceUnknown"):
            print("Please start Spotify first. (%s)"%(dbe.get_dbus_message()))
            sys.exit()
    loop = GLib.MainLoop()

    try:
        print("Start recording on next track.")
        loop.run()
    except KeyboardInterrupt:
        print("Received KeyboardInterrupt. Quiting.")
        if not args.sink:
            pa.unload_combined_sink(combined_sink)
        stop_all()

if __name__ == '__main__':
    main()
