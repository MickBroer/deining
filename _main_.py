import os
import math
from pydub import AudioSegment
from pydub.playback import _play_with_pyaudio
import pyaudio
from datetime import datetime
import wx
import array
import platform
import threading
from mido import Message, MidiFile, MidiTrack

# Definitions
folders = []
audio_folders = []
midi_folders = []
current_folder = None

parameters = [
    "sample", "amplitude", "spacing", "playback_speed", "start", 
    "panning", "duration", "fade_in", "fade_out"
]

midi_parameters = [
    "pitch", "velocity", "notelength", "notespacing"
]

if platform.system() == "Darwin":
    ffmpeg_path = os.path.join(os.getcwd(), "mac", "ffmpeg")
elif platform.system() == "Linux":
    ffmpeg_path = os.path.join(os.getcwd(), "linux", "ffmpeg")
elif platform.system() == "Windows":
    ffmpeg_path = os.path.join(os.getcwd(), "windows", "ffmpeg.exe")
else:
    raise Exception("Unsupported OS")

AudioSegment.converter = ffmpeg_path

class AudioFolder:
    def __init__(self, path=None):
        self.path = path
        self.audio_files = []
        self.formulas = {
            "sample": "x",
            "amplitude": "0.9",
            "spacing": "(x+5)/10",
            "playback_speed": "1.0",
            "start": "x/10%100",
            "panning": "0",
            "duration": "100",
            "fade_in": "0.5",
            "fade_out": "0.5"
        }


    def load_audio_files(self):
        if self.path:
            file_paths = [os.path.join(self.path, file) for file in os.listdir(self.path) if file.endswith('.wav')]
            self.audio_files = [AudioSegment.from_wav(f) for f in file_paths]

class MidiFolder:
    def __init__(self):
        self.midi_files = []  # You can populate this list with MIDI files if needed
        self.formulas = {
            "velocity": "64",  # Default to MIDI velocity 64
            "pitch": "60",  # Default to middle C
            "notelength": "200",  # Default to MIDI channel 1
            "notespacing": "100"
            # Add more MIDI parameters here
        }

def generate_midi_based_on_formula(duration_in_millis, all_folders, filename):
    mid = MidiFile()
    track = MidiTrack()
    mid.tracks.append(track)

    t_millis = 0
    
    while t_millis < duration_in_millis:
        # Use your existing formulas to determine MIDI parameters
        if "pitch" in current_folder.formulas:
            pitch = int(evaluate_formula(current_folder.formulas["pitch"], t_millis, all_folders))
        else:
            pitch = 60

        if "velocity" in current_folder.formulas:
            velocity = int(evaluate_formula(current_folder.formulas["velocity"], t_millis, all_folders))
        else:
            velocity = 60

        if "notelength" in current_folder.formulas:
            notelength = int(evaluate_formula(current_folder.formulas["notelength"], t_millis, all_folders))
        else:
            notelength = 100

        if "notespacing" in current_folder.formulas:
            notespacing = int(evaluate_formula(current_folder.formulas["notespacing"], t_millis, all_folders))
        else:
            notespacing = 100

        # Add MIDI events
        track.append(Message('note_on', note=pitch, velocity=velocity, time=0))
        track.append(Message('note_off', note=pitch, velocity=velocity, time=notelength))

        # Update time
        t_millis += notespacing

    # Save the MIDI file
    mid.save(f"./exports/{filename}")

def get_evaluation_order(folder):
    """Return a list of formula names in the order they should be evaluated."""
    remaining = set(folder.formulas.keys())  # Use the formulas from the passed-in folder
    order = []
    while remaining:
        for formula_name in list(remaining):
            if not get_dependencies(folder.formulas[formula_name]).difference(order):
                order.append(formula_name)
                remaining.remove(formula_name)
    return order

# Functions
def get_evaluation_context(t_millis, all_folders):
    x_value = t_millis / 1000
    context = {"x": x_value}
    
    for i, folder in enumerate(all_folders):
        evaluation_order = get_evaluation_order(folder)
        for formula_name in evaluation_order:
            formula = folder.formulas[formula_name]
            context[f"{i+1}.{formula_name}"] = simple_evaluate(formula, x_value, context)
    
    return context


def multiplier_to_db(multiplier):
    return 20 * math.log10(multiplier)

def get_audio_for_time(audios, formula, t_millis):
    y = eval(formula.replace('x', str(t_millis / 1000)))
    index = int(y) % len(audios)
    return audios[index]



def time_playback_speed(audio, playback_speed_factor):
    """Time-playback_speed an audio segment by the given factor using pydub."""
    if playback_speed_factor < 0:
        audio = audio.reverse()
        playback_speed_factor = abs(playback_speed_factor)
        
    playback_speeded_audio = audio._spawn(audio.raw_data, overrides={
        "frame_rate": int(audio.frame_rate * playback_speed_factor)
    }).set_frame_rate(audio.frame_rate)
    return playback_speeded_audio

def evaluate_formula(formula, t_millis, all_folders):
    context = get_evaluation_context(t_millis, all_folders)
    return eval(formula, {}, context)

def pan_audio(audio, pan_value):
    """Pan an audio segment based on the given pan value (-1 to 1)."""
    if pan_value < -1: pan_value = -1
    if pan_value > 1: pan_value = 1

    # Calculate the gain adjustments for left and right channels
    if pan_value == -1:
        left_gain = 0  # No change in dB
        right_gain = -float('inf')  # Mute
    elif pan_value == 1:
        left_gain = -float('inf')  # Mute
        right_gain = 0  # No change in dB
    else:
        # For pan_value between -1 and 1, we adjust the balance without muting
        left_gain = (1 - pan_value) / 2  # This will range from 0.5 (for pan_value=-1) to 1 (for pan_value=1)
        right_gain = (1 + pan_value) / 2  # This will range from 1 (for pan_value=-1) to 0.5 (for pan_value=1)

    # Convert gains to dB for application
    left_gain_db = 20 * math.log10(left_gain)
    right_gain_db = 20 * math.log10(right_gain)

    # Split the stereo audio into left and right channels
    channels = audio.split_to_mono()
    left_channel = channels[0].apply_gain(left_gain_db)
    right_channel = channels[1].apply_gain(right_gain_db)

    # Combine the adjusted channels back into stereo
    panned_audio = AudioSegment.from_mono_audiosegments(left_channel, right_channel)
    return panned_audio


def fill_audio_based_on_formula(audios, select_formula, gap_formula, playback_speed_formula, start_formula, duration_formula, duration_in_millis, all_folders):
    result = AudioSegment.silent(duration=duration_in_millis)
    t_millis = 0
    audio_for_t = AudioSegment.silent(duration=0) 
    while t_millis < duration_in_millis:
        audio_for_t = get_audio_for_time(audios, select_formula, t_millis)

        audio_for_t = extract_grain(audio_for_t, start_formula, duration_formula, t_millis, all_folders)
        
        # Get fade-in and fade-out percentages
        fade_in_percent = evaluate_formula(current_folder.formulas["fade_in"], t_millis, all_folders)
        fade_out_percent = evaluate_formula(current_folder.formulas["fade_out"], t_millis, all_folders)
        
        # Apply the Hann window with fade-in and fade-out
        windowed_grain = apply_hann_window(audio_for_t, fade_in_percent, fade_out_percent)
        
        # Apply time-playback_speeding
        playback_speed_factor = evaluate_formula(playback_speed_formula, t_millis, all_folders)
        audio_for_t = time_playback_speed(windowed_grain, playback_speed_factor)
        
        # Ensure the audio is in stereo format
        if audio_for_t.channels == 1:
            audio_for_t = audio_for_t.set_channels(2)
        
        # Apply panning
        pan_value = evaluate_formula(current_folder.formulas["panning"], t_millis, all_folders)
        audio_for_t = pan_audio(audio_for_t, pan_value)

        result = result.overlay(audio_for_t, position=t_millis)
        gap_seconds = evaluate_formula(gap_formula, t_millis, all_folders)
        min_gap_seconds = 0.001
        gap_seconds = max(gap_seconds, min_gap_seconds)
        t_millis += int(gap_seconds * 1000)
    return result



def get_evaluation_context(t_millis, all_folders):
    x_value = t_millis / 1000
    context = {"x": x_value}
    
    for i, folder in enumerate(all_folders):
        evaluation_order = get_evaluation_order(folder)
        for formula_name in evaluation_order:
            formula = folder.formulas[formula_name]
            # Use a different key format that won't conflict with Python syntax
            context_key = f"folder_{i+1}_{formula_name}"
            context[context_key] = simple_evaluate(formula, x_value, context)
    
    return context

# Global cache dictionary
formula_cache = {}

def simple_evaluate(formula, x_value, context):
    # Check if result is in cache
    cache_key = (formula, x_value)
    if cache_key in formula_cache:
        return formula_cache[cache_key]
    
    # Ensure that the formula is evaluated in the context of all previously evaluated formulas
    result = eval(formula, {}, context)
    
    # Store result in cache
    formula_cache[cache_key] = result
    
    return result





def apply_hann_window(audio, fade_in_percent, fade_out_percent):
    """Apply a Hann window to the audio segment with fade-in and fade-out."""
    num_samples = len(audio.get_array_of_samples())
    
    # Calculate the number of samples for fade-in and fade-out based on percentages
    fade_in_samples = int((fade_in_percent / 100) * num_samples)
    fade_out_samples = int((fade_out_percent / 100) * num_samples)
    
    # Generate the Hann window
    hann_window = []
    for n in range(num_samples):
        if n < fade_in_samples:
            window_value = 0.5 - 0.5 * math.cos(math.pi * n / fade_in_samples)
        elif n > num_samples - fade_out_samples:
            window_value = 0.5 - 0.5 * math.cos(math.pi * (num_samples - n) / fade_out_samples)
        else:
            window_value = 1
        hann_window.append(window_value)
    
    # Apply the window to the audio samples
    windowed_samples = [int(sample * window_value) for sample, window_value in zip(audio.get_array_of_samples(), hann_window)]
    
    # Convert the windowed samples to bytes
    windowed_bytes = array.array(audio.array_type, windowed_samples).tobytes()
    
    # Convert the windowed bytes back to an AudioSegment
    windowed_audio = audio._spawn(windowed_bytes)
    
    return windowed_audio

def get_dependencies(formula):
    """Return a list of variables (formulas) that the given formula depends on."""
    tokens = formula.split()
    dependencies = set()
    for token in tokens:
        if token in current_folder.formulas and token != "x":
            dependencies.add(token)
    return dependencies

def ensure_exports_folder_exists():
    exports_path = os.path.join(os.getcwd(), "exports")
    if not os.path.exists(exports_path):
        os.makedirs(exports_path)


def extract_grain(audio, start_formula, duration_formula, t_millis, all_folders):
    start_percent = evaluate_formula(start_formula, t_millis, all_folders) / 100
    duration_percent = evaluate_formula(duration_formula, t_millis, all_folders) / 100

    grain_start = int(start_percent * len(audio))
    grain_end = grain_start + int(duration_percent * len(audio))

    # Ensure grain_end doesn't exceed audio length
    grain_end = min(grain_end, len(audio))

    grain = audio[grain_start:grain_end]
    
    # Get fade-in and fade-out percentages
    if "fade_in" in current_folder.formulas:
        fade_in_percent = evaluate_formula(current_folder.formulas["fade_in"], t_millis, all_folders)
    else:
        fade_in_percent = 0.01

    if "fade_out" in current_folder.formulas:
        fade_out_percent = evaluate_formula(current_folder.formulas["fade_out"], t_millis, all_folders)
    else:
        fade_out_percent = 0.01
    
    # Apply the Hann window to the grain
    windowed_grain = apply_hann_window(grain, fade_in_percent, fade_out_percent)
    return windowed_grain



# Options for file formats, bitrates, and sample rates
file_formats = ["wav", "mp3", "flac", "ogg"]
bitrates = ["64k", "128k", "192k", "256k", "320k"]
sample_rates = ["22050", "44100", "48000", "96000"]

class AppFrame(wx.Frame):

    def add_new_folder(self, event):
        global current_folder  # Declare current_folder as global
        dialog = wx.DirDialog(self, "Choose a directory:", style=wx.DD_DEFAULT_STYLE)
        if dialog.ShowModal() == wx.ID_OK:
            path = dialog.GetPath()
            new_folder = AudioFolder(path)
            new_folder.load_audio_files()
            audio_folders.append(new_folder)
            current_folder = new_folder
            self.folder_listbox.Append(os.path.basename(path))
            self.folder_listbox.SetSelection(self.folder_listbox.GetCount() - 1)  # Select the last added entry
            self.update_display()
        dialog.Destroy()

    def add_new_midi_folder(self, event):
        global current_folder  # Declare current_folder as global
        new_midi_folder = MidiFolder()
        midi_folders.append(new_midi_folder)
        current_folder = new_midi_folder
        self.folder_listbox.Append("MIDI")
        self.folder_listbox.SetSelection(self.folder_listbox.GetCount() - 1)  # Select the last added entry
        self.update_display(midi=True)


    def update_formula(self, param, event):
        if self.updating_programmatically:
            return

        if current_folder:
            current_folder.formulas[param] = self.entries[param].GetValue().strip()
            self.update_display()


    def update_display(self, midi=False):
        if midi:
            # Update UI for MIDI-specific parameters
            self.updating_programmatically = True
            for param in midi_parameters:  # Assume midi_parameters is a list of MIDI-specific parameters
                formula = current_folder.formulas.get(param, "")  # Use current_folder, which now can be a MidiFolder
                self.entries[param].SetValue(formula)
            self.updating_programmatically = False
        else:
            if current_folder:
                self.updating_programmatically = True
                for param in parameters:
                    formula = current_folder.formulas.get(param, "")
                    self.entries[param].SetValue(formula)
                self.updating_programmatically = False



    def switch_folder(self, event):
        global current_folder  # Declare current_folder as global
        try:
            index = self.folder_listbox.GetSelection()
            if self.folder_listbox.GetString(index) == "MIDI":
                # Hide audio parameters and show MIDI parameters
                for hbox in self.audio_boxes:
                    hbox.ShowItems(False)
                for hbox in self.midi_boxes:
                    hbox.ShowItems(True)
            else:
                # Hide MIDI parameters and show audio parameters
                for hbox in self.audio_boxes:
                    hbox.ShowItems(True)
                for hbox in self.midi_boxes:
                    hbox.ShowItems(False)

            self.audio_vbox.Layout()  # Explicitly update layout
            self.midi_vbox.Layout()  # Explicitly update layout

            self.Layout()  # Refresh layout
            self.Fit()  # Adjust to content size
            # self.Refresh()
            # self.update_display(midi=(self.folder_listbox.GetString(index) == "MIDI"))
        except Exception as e:
            print(f"An error occurred: {e}")





    def export(self, event):
        ensure_exports_folder_exists()
        duration = self.duration_spin.GetValue()
        combined_audio = AudioSegment.silent(duration=int(duration * 1000))
        for folder in audio_folders:
            folder_audio = fill_audio_based_on_formula(
            folder.audio_files, 
            folder.formulas["sample"], 
            folder.formulas["spacing"], 
            folder.formulas["playback_speed"], 
            folder.formulas["start"], 
            folder.formulas["duration"], 
            int(duration * 1000),
            audio_folders  # Pass all folders here
        )
            amplitude_multiplier = evaluate_formula(folder.formulas["amplitude"], int(duration * 1000), audio_folders)
            gain_db = multiplier_to_db(amplitude_multiplier)
            folder_audio = folder_audio.apply_gain(gain_db)
            combined_audio = combined_audio.overlay(folder_audio)

        current_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        exports_path = os.path.join(os.getcwd(), "exports")
        filename = os.path.join(exports_path, f"combined_output_{current_time_str}." + self.format_dropdown.GetStringSelection())
        
        generate_midi_based_on_formula(int(duration * 1000), midi_folders, f"{current_time_str}.mid")

        combined_audio.export(filename, format=self.format_dropdown.GetStringSelection(), bitrate=self.bitrate_dropdown.GetStringSelection(), parameters=["-ar", str(self.sample_rate_dropdown.GetStringSelection())])
        wx.MessageBox(f"Audio files combined and saved as '{filename}'", 'Info', wx.OK | wx.ICON_INFORMATION)


    def __init__(self, parent, title):
        super(AppFrame, self).__init__(parent, title=title, size=(900, 600))
        self.playing_audio = False
        self.InitUI()

    def play_audio(self, event):
        threading.Thread(target=self.play_audio_in_thread).start()

    def stop_audio(self, event):
        self.playing_audio = False 

    def make_lambda(p):
        return lambda event: self.update_formula(p, event)

    def play_audio_in_thread(self):
        self.playing_audio = True  # Set to True when starting playback
        audio_to_play = self.generate_preview_audio()

        p = pyaudio.PyAudio()
        stream = p.open(format=p.get_format_from_width(audio_to_play.sample_width),
                        channels=audio_to_play.channels,
                        rate=audio_to_play.frame_rate,
                        output=True)

        # Stream the audio in chunks
        for chunk in audio_to_play[::1024]:
            if not self.playing_audio:
                break
            stream.write(chunk._data)

        stream.stop_stream()
        stream.close()
        p.terminate()

    def generate_preview_audio(self):
        duration = self.duration_spin.GetValue()
        combined_audio = AudioSegment.silent(duration=int(duration * 1000))
        for folder in audio_folders:
            folder_audio = fill_audio_based_on_formula(
            folder.audio_files, 
            folder.formulas["sample"], 
            folder.formulas["spacing"], 
            folder.formulas["playback_speed"], 
            folder.formulas["start"], 
            folder.formulas["duration"], 
            int(duration * 1000),
            audio_folders  # Pass all folders here
        )
            amplitude_multiplier = evaluate_formula(folder.formulas["amplitude"], int(duration * 1000))
            gain_db = multiplier_to_db(amplitude_multiplier)
            folder_audio = folder_audio.apply_gain(gain_db)
            combined_audio = combined_audio.overlay(folder_audio)
        return combined_audio

    def InitUI(self):
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        
        
        # Top layout
        hbox1 = wx.BoxSizer(wx.HORIZONTAL)

        # Add Folder button
        select_button = wx.Button(panel, label='Add Audio')
        select_button.Bind(wx.EVT_BUTTON, self.add_new_folder)
        hbox1.Add(select_button, flag=wx.RIGHT, border=10)

        select_midi_button = wx.Button(panel, label='Add MIDI')
        select_midi_button.Bind(wx.EVT_BUTTON, self.add_new_midi_folder)
        hbox1.Add(select_midi_button, flag=wx.RIGHT, border=10)

        # Duration label and spinbox
        duration_label = wx.StaticText(panel, label='Duration:')
        hbox1.Add(duration_label, flag=wx.RIGHT, border=10)

        self.duration_spin = wx.SpinCtrl(panel, value='120', min=1, max=10000)
        hbox1.Add(self.duration_spin, flag=wx.RIGHT, border=10)

        seconds_label = wx.StaticText(panel, label='s')
        hbox1.Add(seconds_label, flag=wx.RIGHT, border=10)

        # Dropdowns for format, bitrate, and sample rate
        self.format_dropdown = wx.Choice(panel, choices=file_formats)
        hbox1.Add(self.format_dropdown, flag=wx.RIGHT, border=10)

        self.bitrate_dropdown = wx.Choice(panel, choices=bitrates)
        hbox1.Add(self.bitrate_dropdown, flag=wx.RIGHT, border=10)

        self.sample_rate_dropdown = wx.Choice(panel, choices=sample_rates)
        hbox1.Add(self.sample_rate_dropdown, flag=wx.RIGHT, border=10)

        self.playback_button = wx.Button(panel, label='Play')
        self.playback_button.Bind(wx.EVT_BUTTON, self.play_audio)
        hbox1.Add(self.playback_button, flag=wx.RIGHT, border=10)  # Added border here

        stop_button = wx.Button(panel, label='Stop')
        stop_button.Bind(wx.EVT_BUTTON, self.stop_audio)
        hbox1.Add(stop_button, flag=wx.RIGHT, border=10)

        # Export button
        export_button = wx.Button(panel, label='Export')
        export_button.Bind(wx.EVT_BUTTON, self.export)
        hbox1.Add(export_button)

        vbox.Add(hbox1, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        # Folder listbox
        self.folder_listbox = wx.ListBox(panel)
        self.folder_listbox.Bind(wx.EVT_LISTBOX, self.switch_folder)
        vbox.Add(self.folder_listbox, proportion=1, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        self.format_dropdown.SetSelection(0)  # Default to 'wav'
        self.bitrate_dropdown.SetSelection(1)  # Default to '128k'
        self.sample_rate_dropdown.SetSelection(1)  # Default to '44100'

        self.audio_vbox = wx.BoxSizer(wx.VERTICAL)
        self.midi_vbox = wx.BoxSizer(wx.VERTICAL)

        # Parameters entries
        self.entries = {}
        self.audio_boxes = []  # List to hold audio parameter boxes
        self.midi_boxes = []  # List to hold MIDI parameter boxes

        for param in parameters:  # Loop for audio parameters
            hbox = wx.BoxSizer(wx.HORIZONTAL)
            label = wx.StaticText(panel, label=param.capitalize())
            hbox.Add(label, flag=wx.RIGHT, border=10)
            entry = wx.TextCtrl(panel, size=(200, -1))
            entry.Bind(wx.EVT_TEXT, lambda event, p=param: self.update_formula(p, event))
            hbox.Add(entry)
            self.entries[param] = entry
            self.audio_boxes.append(hbox)
            self.audio_vbox.Add(hbox, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)
            hbox.ShowItems(True)  # Hide MIDI parameters initially

        for param in midi_parameters:  # Loop for MIDI parameters
            hbox = wx.BoxSizer(wx.HORIZONTAL)
            label = wx.StaticText(panel, label=param.capitalize())
            hbox.Add(label, flag=wx.RIGHT, border=10)
            entry = wx.TextCtrl(panel, size=(200, -1))
            entry.Bind(wx.EVT_TEXT, lambda event, p=param: self.update_formula(p, event))
            hbox.Add(entry)
            self.entries[param] = entry
            self.midi_boxes.append(hbox)
            self.midi_vbox.Add(hbox, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)
            hbox.ShowItems(True)  # Hide MIDI parameters initially

        # Add audio and MIDI vertical box sizers to the main vertical box sizer
        vbox.Add(self.audio_vbox, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)
        vbox.Add(self.midi_vbox, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, border=10)

        panel.SetSizer(vbox)
        self.updating_programmatically = False

        self.Centre()
        self.Show(True)

        


app = wx.App()
AppFrame(None, 'Deining.V1')
app.MainLoop()