#!/usr/bin/env python3
# encoding: utf-8

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
        recording = Recording(self, changed_properties['Metadata'])
        recording.start()
    
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
