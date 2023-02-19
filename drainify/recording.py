#!/usr/bin/env python3
# encoding: utf-8

import re
import dbus
import os
import subprocess
import threading
import datetime

import requests
import mutagen.id3

class Recording:
    """Record spotify stream via pulse audio."""
    def __init__(self, tonmeister, metadata, delay_seconds):
        self.tonmeister = tonmeister
        self.metadata = metadata
        self.ffmpeg = None
        self.reaper = None
        self.filename = self.format_filename(metadata, tonmeister.name_format)
        self.output_path = os.path.join(tonmeister.output_directory, f"{self.sanitize_filename(self.filename)}.mp3")
        self.delay_seconds = delay_seconds
        length_microseconds = self.metadata["mpris:length"]
        self.length_seconds = length_microseconds * 1E-6
        self.end_time = datetime.datetime.now() + datetime.timedelta(seconds=self.length_seconds+self.delay_seconds)

    @staticmethod
    def format_filename(metadata, filename_format):
        """ Prepare file name based on format and metadata. """
        filename = filename_format
        for arg in re.finditer("@[a-z]+", filename_format, re.I):
            a = arg.group()
            s = metadata['xesam:%s'%(a.strip("@"))]
            s = s[0] if s.__class__ == dbus.Array else s # TODO: join for all artists
            filename = filename.replace(a, str(s))
        return filename

    @staticmethod
    def sanitize_filename(filename):
        for o,r in {
            u"/":u"／", # slash is directory separator on unixoid systems
            u"\\":u"＼", # backslash is directory separator on Windows systems. exFAT does not like \
            u"*":u"＊", # exFAT does not like *
            u"?":u"﹖", # FAT32 does not like ?
            u"<":u"‹", # FAT32 does not like >
            u">":u"›", # FAT32 does not like <
            u":":u"：" # FAT32 does not like :
        }.items():
            filename = filename.replace(o, r)
        return filename

    def is_advert(self):
        """ Whether this recording is an advertisement. """
        artist = self.metadata['xesam:artist'][0]
        return artist == ""

    def start(self):
        """ Start recording. """
        if (self.is_advert()):
            print("This is an advertisement. Will not record.")
        elif (self.length_seconds < 1):
            print("Reported length is too short. Not starting to record.")
        elif (self.filename in [r.filename for r in self.tonmeister.recordings]):
            # this is neccessary since the "this song is being played now"
            # message is sometimes received more than once for reasons unknown
            print(f'"{self.filename}" is already being recorded right now. Not starting to record again.')
        elif (os.path.isfile(self.output_path)):
            print(f'"{self.filename}" already exists. Not overwriting.')
        else:
            cmd = self.tonmeister.ffmpeg_command.split()
            cmd = [c
                .replace('@length',f"{self.length_seconds:.2f}")
                .replace('@sink',self.tonmeister.pulseaudio_sink)
                .replace('@file',self.output_path) 
                .replace('@delay',f"{self.delay_seconds:.2f}")
                for c in cmd]
            print("Starting: "+" ".join(cmd))
            self.ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE, encoding='utf-8') # TODO: get system encoding
            # wait in background for the recording to finish
            self.reaper = threading.Thread(target=self.wait)
            self.reaper.start()
            
    def wait(self):
        """ Wait for the recording to end. """
        returncode = self.ffmpeg.wait()
        if (returncode == 0):
            print(f'ffmpeg finished recording "{self.filename}".')
            self.tag_file()
        else:
            print(f'ffmpeg encountered error {returncode} while recording "{self.filename}".')
            self.remove_file()
        
    def is_active(self):
        if (self.ffmpeg is None):
            return False # recording has never started
        else:
            returncode = self.ffmpeg.poll() # poll() returns None while subprocess is still running
            return returncode is None
            
    def is_complete(self):
        return datetime.datetime.now() + datetime.timedelta(seconds=5) > self.end_time

    def abort(self):
        """ Request to abort recording. """
        if (self.is_active() and not self.is_complete()):
            print(f'Abort recording "{self.filename}"...')
            # recording is still running, ask it to terminate
            #print(f'Sending q...')
            #self.ffmpeg.communicate('q\n', timeout=1) # does not suffice
            #print(f'Asking to terminate...')
            #self.ffmpeg.terminate() # does not suffice either
            #print(f'Killing...')
            self.ffmpeg.kill()
            #print(f'Waiting...')
            self.ffmpeg.wait() # wait for it
            print(f'Aborted recording "{self.filename}".')
            
    def remove_file(self):
        print(f'Removing "{self.filename}"...')
        try:
            os.remove(self.output_path)
        except OSError as ose:
            if (ose.errno == 2):
                print(f'Unable to remove file "{self.output_path}". Does not exist.')
            else:
                raise
                
    def tag_file(self):
        audio = mutagen.id3.ID3(self.output_path)
        audio.add(mutagen.id3.TPE1(mutagen.id3.Encoding.UTF8, self.metadata['xesam:artist'][0]))
        audio.add(mutagen.id3.TALB(mutagen.id3.Encoding.UTF8, self.metadata['xesam:album']))
        audio.add(mutagen.id3.TIT2(mutagen.id3.Encoding.UTF8, self.metadata['xesam:title']))
        audio.add(mutagen.id3.TRCK(mutagen.id3.Encoding.UTF8, str(self.metadata['xesam:trackNumber'])))
        if (self.tonmeister.useragent):
            try:
                r = requests.get(self.metadata['mpris:artUrl'], headers = {'user-agent': self.tonmeister.useragent})
                audio.add(mutagen.id3.APIC(
                        encoding = mutagen.id3.Encoding.UTF8,
                        mime = r.headers['Content-Type'], # image/jpeg or image/png
                        type = mutagen.id3.PictureType.COVER_FRONT,
                        desc = 'Cover',
                        data = r.content
                ))
            except Exception as e:
                print(f"Downloading cover art failed: {e}")
        audio.save()
