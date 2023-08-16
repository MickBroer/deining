import os
import math
from pydub import AudioSegment
from datetime import datetime
from tkinter import filedialog
import tkinter as tk
import array
import random
import platform

# Definitions
audio_folders = []
current_folder = None

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
            "rhythm": "(x + 5) / 10",
            "stretch": "1.0",
            "grain start": "x/10%100",
            "panning": "0",
            "grain duration": "100",  # Default to 0% of the audio file
            "fade in": "0.5",  # Default to 0.5 seconds
            "fade out": "0.5"  # Default to 0.5 seconds
        }


    def load_audio_files(self):
        if self.path:
            file_paths = [os.path.join(self.path, file) for file in os.listdir(self.path) if file.endswith('.wav')]
            self.audio_files = [AudioSegment.from_wav(f) for f in file_paths]

def get_evaluation_order():
    """Return a list of formula names in the order they should be evaluated."""
    remaining = set(current_folder.formulas.keys())
    order = []
    while remaining:
        for formula_name in list(remaining):
            if not get_dependencies(current_folder.formulas[formula_name]).difference(order):
                order.append(formula_name)
                remaining.remove(formula_name)
    return order

# Functions
def get_evaluation_context(t_millis):
    x_value = t_millis / 1000
    context = {"x": x_value}
    
    evaluation_order = get_evaluation_order()
    for formula_name in evaluation_order:
        formula = current_folder.formulas[formula_name]
        context[formula_name] = simple_evaluate(formula, x_value, context)
    
    return context





def multiplier_to_db(multiplier):
    return 20 * math.log10(multiplier)

def get_audio_for_time(audios, formula, t_millis):
    y = eval(formula.replace('x', str(t_millis / 1000)))
    index = int(y) % len(audios)
    return audios[index]

def time_stretch(audio, stretch_factor):
    """Time-stretch an audio segment by the given factor using pydub."""
    stretched_audio = audio._spawn(audio.raw_data, overrides={
        "frame_rate": int(audio.frame_rate * stretch_factor)
    }).set_frame_rate(audio.frame_rate)
    return stretched_audio

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


def fill_audio_based_on_formula(audios, select_formula, gap_formula, stretch_formula, start_formula, duration_formula, duration_in_millis):
    result = AudioSegment.silent(duration=duration_in_millis)
    t_millis = 0
    while t_millis < duration_in_millis:
        audio_for_t = get_audio_for_time(audios, select_formula, t_millis)
        
        # Extract grain
        audio_for_t = extract_grain(audio_for_t, start_formula, duration_formula, t_millis)
        
        # Get fade-in and fade-out durations
        fade_in_duration = evaluate_formula(current_folder.formulas["fade in"], t_millis)
        fade_out_duration = evaluate_formula(current_folder.formulas["fade out"], t_millis)
        
        # Apply the Hann window with fade-in and fade-out
        windowed_grain = apply_hann_window(audio_for_t, fade_in_duration, fade_out_duration)
        
        # Apply time-stretching
        stretch_factor = evaluate_formula(stretch_formula, t_millis)
        audio_for_t = time_stretch(windowed_grain, stretch_factor)
        
        # Ensure the audio is in stereo format
        if audio_for_t.channels == 1:
            audio_for_t = audio_for_t.set_channels(2)
        
        # Apply panning
        pan_value = evaluate_formula(current_folder.formulas["panning"], t_millis)
        audio_for_t = pan_audio(audio_for_t, pan_value)

        result = result.overlay(audio_for_t, position=t_millis)
        gap_seconds = evaluate_formula(gap_formula, t_millis)
        min_gap_seconds = 0.001
        gap_seconds = max(gap_seconds, min_gap_seconds)
        t_millis += int(gap_seconds * 1000)
    return result

def evaluate_formula(formula, t_millis):
    context = get_evaluation_context(t_millis)
    return eval(formula, {}, context)

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



def apply_hann_window(audio, fade_in_duration, fade_out_duration):
    """Apply a Hann window to the audio segment with fade-in and fade-out."""
    num_samples = len(audio.get_array_of_samples())
    
    # Calculate the number of samples for fade-in and fade-out
    fade_in_samples = int(fade_in_duration * audio.frame_rate)
    fade_out_samples = int(fade_out_duration * audio.frame_rate)
    
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

def export():
    ensure_exports_folder_exists()
    global duration_spinbox
    duration = int(duration_spinbox.get())
    combined_audio = AudioSegment.silent(duration=int(duration * 1000))
    for folder in audio_folders:
        folder_audio = fill_audio_based_on_formula(
            folder.audio_files, 
            folder.formulas["sample"], 
            folder.formulas["rhythm"], 
            folder.formulas["stretch"], 
            folder.formulas["grain start"], 
            folder.formulas["grain duration"], 
            int(duration * 1000)
        )
        amplitude_multiplier = evaluate_formula(folder.formulas["amplitude"], int(duration * 1000))
        gain_db = multiplier_to_db(amplitude_multiplier)
        folder_audio = folder_audio.apply_gain(gain_db)
        combined_audio = combined_audio.overlay(folder_audio)

    current_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    exports_path = os.path.join(os.getcwd(), "exports")
    filename = os.path.join(exports_path, f"combined_output_{current_time_str}." + selected_format.get())

    combined_audio.export(filename, format=selected_format.get(), bitrate=selected_bitrate.get(), parameters=["-ar", str(selected_sample_rate.get())])
    print(f"Audio files combined and saved as '{filename}'")


def extract_grain(audio, start_formula, duration_formula, t_millis):
    start_percent = evaluate_formula(start_formula, t_millis) / 100
    duration_percent = evaluate_formula(duration_formula, t_millis) / 100

    grain_start = int(start_percent * len(audio))
    grain_end = grain_start + int(duration_percent * len(audio))

    # Ensure grain_end doesn't exceed audio length
    grain_end = min(grain_end, len(audio))

    grain = audio[grain_start:grain_end]
    
    # Get fade-in and fade-out durations
    fade_in_duration = evaluate_formula(current_folder.formulas["fade in"], t_millis)
    fade_out_duration = evaluate_formula(current_folder.formulas["fade out"], t_millis)
    
    # Apply the Hann window to the grain
    windowed_grain = apply_hann_window(grain, fade_in_duration, fade_out_duration)
    
    return windowed_grain





def add_new_folder():
    global current_folder
    path = filedialog.askdirectory()
    if path:
        new_folder = AudioFolder(path)
        new_folder.load_audio_files()
        audio_folders.append(new_folder)
        current_folder = new_folder
        
        # Insert and select the newly added folder in the listbox
        folder_listbox.insert(tk.END, os.path.basename(path))
        folder_listbox.selection_set(tk.END)  # Select the last added entry
        
        # Enable the entry boxes and display default formulas
        for entry in entries.values():
            entry.config(state=tk.NORMAL)
        update_display()

def update_formula(event, param):
    if current_folder:
        # Only update the formula for printable characters and specific keys.
        if event.char.isprintable() or event.keysym in ["BackSpace", "Delete", "Enter"]:
            current_folder.formulas[param] = entries[param].get().strip()
            update_display()

def update_display():
    if current_folder:
        for param in parameters:
            formula = current_folder.formulas.get(param, "")
            entries[param].delete(0, tk.END)
            entries[param].insert(0, formula)

def switch_folder(event):
    global current_folder
    try:
        index = folder_listbox.curselection()[0]
        current_folder = audio_folders[index]
        update_display()
    except:
        pass

# Options for file formats, bitrates, and sample rates
file_formats = ["wav", "mp3", "flac", "ogg"]
bitrates = ["64k", "128k", "192k", "256k", "320k"]
sample_rates = ["22050", "44100", "48000", "96000"]

# UI Components
root = tk.Tk()
root.geometry("900x600")  # Adjusted to fit new components
root.resizable(False, False)
root.title("deining")

duration = 120

main_frame = tk.Frame(root)
main_frame.pack(fill="both", expand=True)

# Add Folder button
select_button = tk.Button(main_frame, text="Add Folder", width=10, command=add_new_folder)
select_button.grid(row=0, column=0, padx=20, pady=20, sticky="w")

# Duration label and spinbox
duration_label = tk.Label(main_frame, text="Duration:")
duration_label.grid(row=0, column=1, padx=10, pady=20, sticky="w")

duration_spinbox = tk.Spinbox(main_frame, from_=1, to=10000, width=7)
duration_spinbox.grid(row=0, column=1, padx=(80,0), pady=20, sticky="w")
duration_spinbox.delete(0, tk.END)
duration_spinbox.insert(0, duration)

seconds_label = tk.Label(main_frame, text="s")
seconds_label.grid(row=0, column=1, padx=(170,0), pady=20, sticky="w")

# Variables to store the selected values
selected_format = tk.StringVar(root)
selected_format.set(file_formats[0])  # default value

selected_bitrate = tk.StringVar(root)
selected_bitrate.set(bitrates[1])  # default value

selected_sample_rate = tk.StringVar(root)
selected_sample_rate.set(sample_rates[1])  # default value

# Create the dropdown menus
format_menu = tk.OptionMenu(main_frame, selected_format, *file_formats)
format_menu.grid(row=0, column=2, padx=10, pady=20, sticky="w")

bitrate_menu = tk.OptionMenu(main_frame, selected_bitrate, *bitrates)
bitrate_menu.grid(row=0, column=2, padx=(0,200), pady=20, sticky="e")

sample_rate_menu = tk.OptionMenu(main_frame, selected_sample_rate, *sample_rates)
sample_rate_menu.grid(row=0, column=2, padx=(0,100), pady=20, sticky="e")

# Export button
export_button = tk.Button(main_frame, text="Export", width=10, command=export)
export_button.grid(row=0, column=0, padx=(0,100), pady=20, sticky="e")

# Folder listbox
folder_listbox = tk.Listbox(main_frame, width=30)
folder_listbox.grid(row=1, column=0, padx=20, pady=5, rowspan=6, sticky="w")
folder_listbox.bind("<<ListboxSelect>>", switch_folder)

# Parameters entries
parameters = ["sample", "amplitude", "rhythm", "stretch", "grain start", "grain duration", "panning", "fade in", "fade out"]
entries = {}  # store references to entries for later retrieval of their values

for idx, param in enumerate(parameters):
    label = tk.Label(main_frame, text=param.capitalize())
    label.grid(row=idx+1, column=1, padx=10, pady=5, sticky="w")
    y_label = tk.Label(main_frame, text="y=")
    y_label.grid(row=idx+1, column=1, padx=(170,0), pady=5, sticky="w")
    entry = tk.Entry(main_frame, width=35, state=tk.DISABLED)
    entry.grid(row=idx+1, column=2, padx=10, pady=5, sticky="w")
    entry.bind("<KeyRelease>", lambda event, p=param: update_formula(event, p))
    entries[param] = entry

root.mainloop()
