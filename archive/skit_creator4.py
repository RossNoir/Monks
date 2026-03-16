import sys
import os
import re
import tempfile
import json
import time
import requests
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QLabel, QLineEdit,
    QProgressBar, QFormLayout, QGroupBox, QComboBox
)
from pydub import AudioSegment
from pydub.effects import normalize # <<< NEW >>> Import normalize
import ollama

# --- F5 TTS Integration ---
try:
    from api import F5TTS
except ImportError:
    print("FATAL: Could not import F5TTS from api.py.")
    print("Please ensure api.py is in the same directory as this script.")
    F5TTS = None

# --- Configuration ---
CONFIG_FILE = "skit_creator_config.json"
TRANSCRIPT_CACHE_FILE = "transcript_cache.json"
PROMPT_CONFIG_FILE = "prompt_config.json"
OUTPUT_FOLDER = "skits" # <<< NEW >>> Define output folder

# --- Hardcoded Voice File Configuration ---
VOICE_FILES = {
    "JERRY": "voices/jerry.wav",
    "GEORGE": "voices/george.wav",
    "ELAINE": "voices/elaine.wav",
    "KRAMER": "voices/kramer.wav"
}
CHARACTERS = list(VOICE_FILES.keys())

class SkitCreator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Seinfeld Skit Creator Alpha1")
        self.setGeometry(100, 100, 750, 780) # Adjusted height

        self.f5_model = None
        self.voice_transcripts = {}
        self.ollama_models = []
        self.skit_prompt = ""

        self.load_or_create_prompt_config()
        if F5TTS:
            self.manage_voice_transcripts()

        # --- UI Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        model_group_box = QGroupBox("AI Model Selection")
        model_layout = QFormLayout()
        self.model_selector = QComboBox()
        self.batch_selector = QComboBox() # <<< NEW >>> Batch selector dropdown
        self.batch_selector.addItems(["1", "3", "5", "10"]) # <<< NEW >>> Batch options
        self.populate_ollama_models()
        model_layout.addRow("Generator Model:", self.model_selector)
        model_layout.addRow("Number of Skits:", self.batch_selector) # <<< NEW >>> Add to UI
        model_group_box.setLayout(model_layout)
        main_layout.addWidget(model_group_box)

        self.character_widgets = {}
        for character in CHARACTERS:
            group_box = QGroupBox(character)
            form_layout = QFormLayout()
            speed_widget = QLineEdit("1.0")
            seed_widget = QLineEdit("-1")
            form_layout.addRow("Speech Speed:", speed_widget)
            form_layout.addRow("Voice Seed:", seed_widget)
            group_box.setLayout(form_layout)
            main_layout.addWidget(group_box)
            self.character_widgets[character] = { "speed": speed_widget, "seed": seed_widget }

        self.intro_path = QLineEdit()
        self.ender_path = QLineEdit()
        self.dialogue_delay_widget = QLineEdit("5.0")
        music_group_box = QGroupBox("Audio Production")
        music_layout = QFormLayout()
        music_layout.addRow("Intro Music Bed (.mp3):", self.create_file_selector(self.intro_path))
        music_layout.addRow("Ender Audio (.mp3):", self.create_file_selector(self.ender_path))
        music_layout.addRow("Dialogue Delay (seconds):", self.dialogue_delay_widget)
        music_group_box.setLayout(music_layout)
        main_layout.addWidget(music_group_box)

        self.generate_button = QPushButton("Generate Skit(s)")
        self.generate_button.clicked.connect(self.generate_skit_batch)
        main_layout.addWidget(self.generate_button)

        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)

        self.load_config()
        
        if any(char not in self.voice_transcripts for char in CHARACTERS):
             self.generate_button.setEnabled(False)
             self.generate_button.setText("Voice file(s) missing, check console.")
        
        if self.model_selector.count() == 0:
            self.generate_button.setEnabled(False)
            self.generate_button.setText("Ollama not running or no models found.")

    def load_or_create_prompt_config(self):
        default_prompt = (
            'You are an expert scriptwriter for a "Seinfeld"-style comedy radio play... (prompt text)'
        )
        try:
            with open(PROMPT_CONFIG_FILE, 'r') as f:
                config = json.load(f)
                self.skit_prompt = config.get("seinfeld_skit_prompt", default_prompt)
            print(f"--- Loaded custom prompt from {PROMPT_CONFIG_FILE} ---")
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"--- No valid prompt config found. Creating '{PROMPT_CONFIG_FILE}' with default prompt. ---")
            self.skit_prompt = default_prompt
            with open(PROMPT_CONFIG_FILE, 'w') as f:
                json.dump({"seinfeld_skit_prompt": default_prompt}, f, indent=4)

    def populate_ollama_models(self):
        try:
            print("--- Checking for local Ollama models via API... ---")
            response = requests.get('http://127.0.0.1:11434/api/tags')
            response.raise_for_status() 
            models_data = response.json()['models']
            self.ollama_models = [model['name'] for model in models_data]
            if self.ollama_models:
                self.model_selector.addItems(self.ollama_models)
                print(f"  -> Found models: {', '.join(self.ollama_models)}")
            else:
                print("  -> No Ollama models found.")
        except requests.exceptions.ConnectionError:
            self.ollama_models = []
            print("ERROR: Could not connect to Ollama server.")
            self.show_message("Ollama Connection Error", "Could not connect to the Ollama server. Please ensure it is running and restart this application.")
        except Exception as e:
            self.ollama_models = []
            print(f"An error occurred while getting Ollama models: {e}")

    def manage_voice_transcripts(self):
        print("--- Checking Voice File Transcripts ---")
        self.initialize_f5_model()
        
        cache = {}
        if os.path.exists(TRANSCRIPT_CACHE_FILE):
            with open(TRANSCRIPT_CACHE_FILE, 'r') as f: cache = json.load(f)

        updated_cache, needs_update = {}, False
        for char, file_path in VOICE_FILES.items():
            if not os.path.exists(file_path):
                print(f"ERROR: Voice file for {char} not found at '{file_path}'.")
                continue

            current_mod_time = os.path.getmtime(file_path)
            cached_entry = cache.get(file_path)

            if cached_entry and cached_entry.get("mod_time") == current_mod_time:
                transcript = cached_entry["transcript"]
            else:
                print(f"  -> Analyzing new/updated voice file for '{char}'...")
                QApplication.processEvents()
                try:
                    transcript = self.f5_model.transcribe(file_path)
                    print(f"     ...Done. Transcript: '{transcript}'")
                    needs_update = True
                except Exception as e:
                    self.show_message("Transcription Error", f"Could not analyze the voice file for {char}.")
                    continue

            self.voice_transcripts[char] = transcript
            updated_cache[file_path] = {"transcript": transcript, "mod_time": current_mod_time}
        
        if needs_update:
            print(f"--- Saving updated transcripts to '{TRANSCRIPT_CACHE_FILE}' ---")
            with open(TRANSCRIPT_CACHE_FILE, 'w') as f: json.dump(updated_cache, f, indent=4)

    def create_file_selector(self, line_edit_widget):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit_widget)
        button = QPushButton("Browse...")
        button.clicked.connect(lambda: self.browse_for_file(line_edit_widget))
        layout.addWidget(button)
        return widget

    def browse_for_file(self, line_edit_widget):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if file_path: line_edit_widget.setText(file_path)

    def initialize_f5_model(self):
        if self.f5_model is None:
            print("--- Initializing F5 TTS Engine (this may take a moment) ---")
            try:
                self.f5_model = F5TTS()
                print("--- F5 TTS Engine loaded successfully. ---")
            except Exception as e:
                self.show_message("F5 TTS Error", f"Could not initialize the F5 TTS model: {e}")
                return False
        return True

    def generate_skit_batch(self): # <<< UPDATED >>> Main logic now handles batches
        # --- 1. Validate Inputs ---
        character_data = {}
        for char, widgets in self.character_widgets.items():
            try:
                character_data[char] = {
                    "ref_audio": VOICE_FILES[char], "ref_text": self.voice_transcripts[char],
                    "speed": float(widgets["speed"].text()), "seed": int(widgets["seed"].text())
                }
            except (ValueError, KeyError) as e:
                self.show_message("Invalid Input", f"The settings for {char} are invalid. Error: {e}")
                return

        try:
            dialogue_delay_sec = float(self.dialogue_delay_widget.text())
        except ValueError:
            self.show_message("Invalid Input", "Dialogue Delay must be a number.")
            return

        intro_file, ender_file = self.intro_path.text(), self.ender_path.text()
        if not all([intro_file, ender_file]):
            self.show_message("Missing Information", "Please select an Intro Music Bed and an Ender Audio file.")
            return

        batch_count = int(self.batch_selector.currentText())
        os.makedirs(OUTPUT_FOLDER, exist_ok=True) # <<< NEW >>> Create output folder
        
        self.progress_bar.setValue(0)
        self.generate_button.setEnabled(False)
        QApplication.processEvents()
        
        if not self.initialize_f5_model():
             self.generate_button.setEnabled(True)
             return

        # --- 2. Loop for Batch Generation ---
        for i in range(batch_count):
            current_batch_num = i + 1
            print(f"\n--- Generating Skit {current_batch_num} of {batch_count} ---")
            
            dialogue = self.generate_seinfeld_skit()
            if not dialogue:
                self.show_message("Error", f"Failed to generate dialogue for skit {current_batch_num}. Aborting batch.")
                break 
            
            # Update progress after script generation
            self.progress_bar.setValue(int(((i + 0.25) / batch_count) * 100))

            print(f"Generating audio for skit {current_batch_num}...")
            dialogue_segments, temp_files_to_clean = [], []
            total_lines = len(dialogue)
            
            for line_idx, line in enumerate(dialogue):
                speaker, text = line["speaker"], line["line"]
                if speaker not in character_data: continue

                speaker_info = character_data[speaker]
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_f:
                    temp_line_file = temp_f.name
                    temp_files_to_clean.append(temp_line_file)
                
                try:
                    self.f5_model.infer(
                        ref_file=speaker_info["ref_audio"], ref_text=speaker_info["ref_text"],
                        gen_text=text, speed=speaker_info["speed"],
                        file_wave=temp_line_file, seed=speaker_info["seed"]
                    )
                    dialogue_segments.append(AudioSegment.from_wav(temp_line_file))
                except Exception as e:
                    self.show_message("TTS Error", f"Failed TTS for {speaker} in skit {current_batch_num}.\nError: {e}")
                    # Clean up and abort this specific skit
                    for f in temp_files_to_clean: os.remove(f)
                    dialogue_segments = None
                    break
            
            if dialogue_segments is None: continue # Move to next skit in batch if one failed

            # Update progress after TTS generation for this skit
            self.progress_bar.setValue(int(((i + 0.85) / batch_count) * 100))

            try:
                print(f"Assembling final audio for skit {current_batch_num}...")
                intro_music_bed = AudioSegment.from_mp3(intro_file)
                ender_segment = AudioSegment.from_mp3(ender_file)
                pause = AudioSegment.silent(duration=300)

                full_dialogue_no_delay = sum(dialogue_segments, AudioSegment.empty())
                for seg in dialogue_segments: full_dialogue_no_delay += pause
                
                initial_delay = AudioSegment.silent(duration=int(dialogue_delay_sec * 1000))
                full_dialogue = initial_delay + full_dialogue_no_delay

                music_bed_trimmed = intro_music_bed[:len(full_dialogue)]
                dialogue_on_bed = (music_bed_trimmed - 6).overlay(full_dialogue)
                final_segment = dialogue_on_bed + ender_segment

                print("Normalizing audio...") # <<< NEW >>> Normalization step
                normalized_segment = normalize(final_segment)

                timestamp = int(time.time()) # <<< NEW >>> Auto-generate filename
                save_path = os.path.join(OUTPUT_FOLDER, f"skit_{timestamp}_{current_batch_num}.mp3")
                
                print(f"Exporting to {save_path}...")
                normalized_segment.export(save_path, format="mp3")

            except Exception as e:
                self.show_message("Audio Assembly Error", f"Failed to assemble skit {current_batch_num}: {e}")
            finally:
                for f in temp_files_to_clean:
                    try: os.remove(f)
                    except OSError: pass
            
            self.progress_bar.setValue(int((current_batch_num / batch_count) * 100))

        # --- 3. Finalize ---
        self.generate_button.setEnabled(True)
        self.show_message("Success", f"Batch completed.\n{batch_count} skit(s) saved to the '{OUTPUT_FOLDER}' folder.")

    def generate_seinfeld_skit(self):
        selected_model = self.model_selector.currentText()
        if not selected_model: return None
        try:
            prompt = self.skit_prompt
            response = ollama.chat(model=selected_model, messages=[{'role': 'user', 'content': prompt}])
            full_script = response['message']['content'].strip()
            print(f"--- Generated Script ---\n{full_script}\n------------------------")
            parsed_dialogue = []
            for line in full_script.split('\n'):
                match = re.match(r'^(' + '|'.join(CHARACTERS) + r'):\s*(.*)', line.strip())
                if match: parsed_dialogue.append({"speaker": match.group(1), "line": match.group(2).strip()})
            return parsed_dialogue
        except Exception as e:
            print(f"Failed to generate script from Ollama model '{selected_model}': {e}")
            return None

    def show_message(self, title, message):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.exec()

    def save_config(self):
        config_data = {
            "ai_model": self.model_selector.currentText(),
            "batch_count": self.batch_selector.currentText(), # <<< NEW >>> Save batch count
            "characters": {},
            "audio_production": {
                "intro_file": self.intro_path.text(), "ender_file": self.ender_path.text(),
                "dialogue_delay": self.dialogue_delay_widget.text()
            }
        }
        for char, widgets in self.character_widgets.items():
            config_data["characters"][char] = { "speed": widgets["speed"].text(), "seed": widgets["seed"].text() }
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)
        except Exception as e: print(f"Error saving config file: {e}")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f: config_data = json.load(f)
                
                saved_model = config_data.get("ai_model")
                if saved_model: self.model_selector.setCurrentText(saved_model)

                saved_batch_count = config_data.get("batch_count") # <<< NEW >>> Load batch count
                if saved_batch_count: self.batch_selector.setCurrentText(saved_batch_count)
                
                char_data = config_data.get("characters", {})
                for char, widgets in self.character_widgets.items():
                    data = char_data.get(char, {})
                    widgets["speed"].setText(data.get("speed", "1.0"))
                    widgets["seed"].setText(data.get("seed", "-1"))
                
                prod_data = config_data.get("audio_production", {})
                self.intro_path.setText(prod_data.get("intro_file", ""))
                self.ender_path.setText(prod_data.get("ender_file", ""))
                self.dialogue_delay_widget.setText(prod_data.get("dialogue_delay", "5.0"))
            except Exception as e: print(f"Error loading config file: {e}")

    def closeEvent(self, event):
        self.save_config()
        event.accept()

if __name__ == "__main__":
    if F5TTS is None: sys.exit(1)
    app = QApplication(sys.argv)
    creator = SkitCreator()
    creator.show()
    sys.exit(app.exec())

