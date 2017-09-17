#!/usr/bin/env python
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
import urllib2

import dbus
import dbus.mainloop.glib
import eyed3
import gobject
import threading

import pa

# url prefix for album covers
IMG_PREFIX = "https://d3rt1990lpmkn.cloudfront.net/320/"

rec_dir = os.getcwd()
pa_sink = "combined.monitor"

# running recorders
running_recs = []

def set_id3_tags(filename, metadata):
    """Set the ID3 tags for an audio file.

    :filename: location of the audio file
    :metadata: contains information for the audio file from the dbus event

    """
    try:
        audiofile = eyed3.load(filename)
    except UnicodeEncodeError as uee:
        # I have no idea why this happens
        return
    except (OSError, IOError) as err:
        # this can happen when running recording is aborted
        if (err.errno == 2):
            print("Unable to tag file \"%s\". Does not exist."%(self.final_path))
        else:
            raise
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
        image_data = urllib2.urlopen(art_url).read()
        audiofile.tag.images.set(3, image_data, "image/jpeg", u"")
    except ValueError as ve:
        # this can happen upon _, cover_id = cover_art_url.rsplit('/', 1)
        pass

    try:
        audiofile.tag.save()
    except UnicodeEncodeError as uee:
        print("Writing tags failed due to UnicodeEncodeError.")
        # this happens with artist alike Röyskopp
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

        self.name = self.format_name(name_format)
        self.final_path = os.path.join(rec_dir,"%s.mp3"%(self.name))
        
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
            s = str(s)
            name = name.replace(a,s)
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
        else:
            # TODO: make cmd-string configurable
            cmd = ("ffmpeg -hide_banner -loglevel fatal -f pulse -ac 2 -i %s -c:a libmp3lame -qscale:a 3 -y -t %f"%(pa_sink,self.length_seconds)).split()
            cmd.append(self.final_path)
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
            except (OSError, IOError) as err:
                if (err.errno == 2):
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
        if metadata['PlaybackStatus'] == 'Stopped':
            stop_all()
            return
        if metadata['PlaybackStatus'] == 'Playing':
            # TODO: skipping tracks does not work cleanly (damaged recording is not removed)
            # this is fired on queue changes (i.e. adding songs for later), too
            if 'Metadata' not in metadata:
                # I never actually observed this case
                print("No Metadata")
                return
    else:
        # I never actually observed this case
        print("No PlaybackStatus")
        return

    rec = Recorder(metadata, running_recs)
    if (running_recs):
        # this is not the first song being recorded
        sleeptime = 1.5 # TODO: make configurable
        try:
            if (running_recs[-1].is_advert()):
                # after an ad, sleep some more
                sleeptime += 0.5 # sometimes 0.5 is not enough
        except IndexError:
            # poor thread-safety: this happens if a recording finished between if (running_recs) and access to running_recs[-1]
            pass
        # do not record the end of the last song, so sleep
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
    parser = argparse.ArgumentParser("Record you tracks playing with spotify on pulseaudio.")

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

    args = parser.parse_args()
    if args.dir:
        if not os.path.exists(args.dir):
            create_dir = raw_input("Directory doesn't exist. Create? [y/n] ")
            if create_dir == 'y':
                os.mkdir(args.dir)
            else:
                sys.exit()
        global rec_dir
        rec_dir = os.path.abspath(args.dir)
        
    if args.name:
        global name_format
        name_format = args.name
    
    if args.sink:
        global pa_sink
        pa_sink = args.sink
        print("Recording from explicitly set sink %s."%(pa_sink))
    else:
        print("Looking for direct connection to spotify audio stream...")
        # init combined sink
        sinks = pa.list_sinks()
        sink_choose = None
        if len(sinks) > 1:
            for i, s in enumerate(sinks):
                print("%i: %s" % (i, s))
            sink_choose = raw_input("Choose your audio device (Default [0]): ")
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
    loop = gobject.MainLoop()

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
