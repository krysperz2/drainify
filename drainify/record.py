#!/usr/bin/env python
# encoding: utf-8
from __future__ import print_function

import argparse
import datetime
import os
import time
import shutil
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

# TODO: make not global
rec_dir = os.getcwd()
pa_sink = "combined.monitor"

# running recorders
running_recs = {}


def set_id3_tags(filename, metadata):
    """Setting the ID3 tags for an audio file.

    :filename: location of the audio file
    :metadata: contains information for the audio file from the dbus event

    """
    try:
        audiofile = eyed3.load(filename)
    except UnicodeEncodeError as uee:
        # I have no idea why this happens
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
        image_data = urllib2.urlopen(art_url).read()
        audiofile.tag.images.set(3, image_data, "image/jpeg", u"")
    except ValueError as ve:
        # this can happen upon _, cover_id = cover_art_url.rsplit('/', 1)
        pass

    try:
        audiofile.tag.save()
    except UnicodeEncodeError as uee:
        print("writing tags failed due to UnicodeEncodeError")
        # this happens with Artist Röyskopp
        pass


class Recorder(object):
    """Recording the spotify throught pulse audio."""
    def __init__(self, ffmpeg, metadata, filename, final_path):
        """Recording the stream.

        :ffmpeg:  ffmpeg recording and encoding subprocess
        :metadata: track information from dbus event
        :final_path: final file path for the recording

        """
        self.ffmpeg = ffmpeg
        self.metadata = metadata
        self.filename = filename
        self.final_path = final_path

    def stop_handler(self):
        """ Killing running recorders immediately """
        running_recs.pop(self.filename)
        if (self.ffmpeg.poll() is None):
            os.killpg(self.ffmpeg.pid, signal.SIGKILL)
        # TODO: check subprocess status, remove file only if record aborted
        # remove temporary file
        print("Would now remove %s"%(self.filename))
        #try:
        #    os.remove(self.final_path)
        #except OSError as ose:
        #    if (ose.errno == 2):
        #        print("Unable to remove file %s. Does not exist."%(self.final_path))
        #    else:
        #        raise

    def wait_recording(self):
        """ Callback for stopping the recording.
        Setting ID3 Tags.
        Moves the temp file to the specific directory.
        """
        # waiting for encoder to encode the end of stream
        self.ffmpeg.wait()
        print("finished recording of %s." % (self.filename))
        self.stop_handler()
        
        set_id3_tags(self.final_path, self.metadata['Metadata'])


# TODO: skipping tracks dont work
def recording_handler(sender=None, metadata=None, sig=None):
    if "PlaybackStatus" in metadata:
        print(metadata['PlaybackStatus'])
        if metadata['PlaybackStatus'] == 'Paused':
            cleanup()
            return
        if metadata['PlaybackStatus'] == 'Stopped':
            cleanup()
            return
        if metadata['PlaybackStatus'] == 'Playing':
            if 'Metadata' not in metadata:
                print("Metadata")
                return
            else:
                # message contains metadata and PlaybackStatus means track skipping
                # TODO: call cleanup() ?
                pass
                print("No Metadata")
    else:
        print("No PlaybackStatus")

    # TODO: move into Recorder.__init__()
    title = metadata['Metadata']['xesam:title']
    album = metadata['Metadata']['xesam:album']
    artist = metadata['Metadata']['xesam:artist'][0]
    filename = u"%s - %s - %s" % (artist, album, title)
    print("recording: %s"%(filename))

    final_path = os.path.join(rec_dir,"%s.mp3"%(filename))
    
    length = metadata['Metadata']["mpris:length"]
    # avoid recording the beginning of the next track
    secs = length * 1E-6
    secs -= 0.750

    if (running_recs):
        # this is not the first song being recorded
        # do not record the end of the last song, so sleep
        # TODO: make configurable
        time.sleep(1.5)
        #secs -= 1.5

    # TODO: move into Recorder.start()
    if (secs < 1):
        print("Reported length is too short. Not starting to record.")
    elif (filename in running_recs):
        print("No, wait - this is already being recorded right now. "
        "Not starting to record again.")
    else:
        cmd = ("ffmpeg -hide_banner -loglevel fatal -f pulse -ac 2 -i %s -c:a libmp3lame -qscale:a 3 -y -t %f"%(pa_sink,secs)).split()
        cmd.append(final_path)
        print(" ".join(cmd))
        ffmpeg = subprocess.Popen(cmd,preexec_fn=os.setsid)

        r = Recorder(ffmpeg, metadata, filename, final_path)
        running_recs[filename] = r
        t = threading.Thread(target=r.wait_recording)
        t.start()

def debug_handler(sender=None, metadata=None, k2=None):
    print(datetime.datetime.now(), "got signal from ", sender)
    print(metadata.keys())
    print(k2)
    print("")


def cleanup():
    """Kill all running recordings."""
    for rec in running_recs.values():
        rec.stop_handler()
    print("Stopped all recordings.")


def main():
    parser = argparse.ArgumentParser("Record you tracks playing with spotify on pulseaudio.")

    parser.add_argument('--dir',
                        '-d',
                        help="Directory for storing files. "
                        "(Default: current directory)",
                        type=str)

    parser.add_argument('--sink',
                        '-s',
                        help="Pulseaudio sink to record from. "
                        "(Default: search for spotify in particular)",
                        type=str)

    args = parser.parse_args()
    if args.dir:
        if not os.path.exists(args.dir):
            create_dir = raw_input("Directory doesn't exists. Create? [y/n] ")
            if create_dir == 'y':
                os.mkdir(args.dir)
            else:
                sys.exit()

        global rec_dir
        rec_dir = os.path.abspath(args.dir)
    
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
        remote_object = bus.get_object("org.mpris.MediaPlayer2.spotify",
                                       "/org/mpris/MediaPlayer2")
        change_manager = dbus.Interface(remote_object,
                                        'org.freedesktop.DBus.Properties')
        change_manager.connect_to_signal("PropertiesChanged",
                                         recording_handler)
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
        cleanup()

if __name__ == '__main__':
    main()
