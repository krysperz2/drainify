#!/usr/bin/env python3
# encoding: utf-8

import os
import threading
from .recording import Recording

class Tonmeister:
    def __init__(self, dir:str, name:str, command:str, sink:str, delay:float, useragent:str):
        self.output_directory = dir
        self.name_format = name
        self.ffmpeg_command = command
        self.pulseaudio_sink = sink
        self.record_delay_seconds = delay
        self.useragent = useragent
        self.recordings = []
        
    def on_properties_changed(self, interface_name=None, changed_properties=None, invalidated_properties=None):
        if "PlaybackStatus" in changed_properties and changed_properties['PlaybackStatus'] in ['Paused','Stopped']:
            self.stop_all()
            return
        if 'Metadata' not in changed_properties:
            print("No information about the current song. Skip to next song. Add current song to queue to try again.")
            return
        metadata = changed_properties['Metadata']
        delay_seconds = self.record_delay_seconds
        if (not self.recordings):
            print("This is the first recording, starting without delay.")
            delay_seconds = 0
        elif (not self.recordings[-1].is_complete()):
            print("Current recording is incomplete, song was probably skipped, recording next one without delay.")
            delay_seconds = 0
        recording = Recording(self, metadata, delay_seconds)
        if (recording.is_advert()):
            print("This is an advertisement. Will not record.")
            return
        if (recording.length_seconds < 1):
            print("Reported length is too short. Not starting to record.")
            return
        if (recording.filename in [r.filename for r in self.recordings]):
            # this is neccessary since the "this song is being played now"
            # message is sometimes received more than once for reasons unknown
            print(f'"{recording.filename}" is already being recorded right now. Not starting to record again.')
        elif (os.path.isfile(recording.output_path)):
            print(f'"{recording.filename}" already exists. Not overwriting.')
        recording.start()
        self.stop_all()
        self.recordings.append(recording)
    
    def stop_all(self):
        aborters = []
        recordings = [r for r in self.recordings if r.is_active()]
        if (recordings):
            print(f"Stopping {len(recordings)} active recording(s)...")
            for r in recordings:
                t = threading.Thread(target=r.abort)
                aborters.append(t)
                t.start()
        if (aborters):
            print(f"Waiting for {len(aborters)} thread(s) to settle...")
            for t in aborters:
                t.join()
