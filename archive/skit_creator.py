import sys
import os
import re
import tempfile
import json
import time
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QLabel, QLineEdit,
    QProgressBar, QFormLayout, QGroupBox
)
from pydub import AudioSegment
import ollama

# --- F5 TTS Integration ---
try:
    from api import F5TTS
except ImportError:
    print("FATAL: Could not import F5TTS from api.py.")
    print("Please ensure api.py is in the same directory as this script.")
    F5TTS = None

# --- Configuration ---
MODEL_TO_USE = "gemma3:4b"
CONFIG_FILE = "skit_creator_config.json"
TRANSCRIPT_CACHE_FILE = "transcript_cache.json"

# --- Hardcoded Voice File Configuration ---
# Place your reference .wav files in a 'voices' subfolder
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
        self.setWindowTitle("Direct Sound Radio - Skit Creator")
        self.setGeometry(100, 100, 750, 650) # Adjusted height for simpler UI

        self.f5_model = None
        self.voice_transcripts = {}

        # --- Automated Transcription on Startup ---
        if F5TTS:
            self.manage_voice_transcripts()

        # --- UI Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.character_widgets = {}
        for character in CHARACTERS:
            group_box = QGroupBox(character)
            form_layout = QFormLayout()

            speed_widget = QLineEdit("1.0")
            seed_widget = QLineEdit("-1") # -1 for random

            form_layout.addRow("Speech Speed:", speed_widget)
            form_layout.addRow("Voice Seed:", seed_widget)

            group_box.setLayout(form_layout)
            main_layout.addWidget(group_box)

            self.character_widgets[character] = {
                "speed": speed_widget,
                "seed": seed_widget
            }

        self.intro_path = QLineEdit()
        self.ender_path = QLineEdit()
        music_group_box = QGroupBox("Audio Production")
        music_layout = QFormLayout()
        music_layout.addRow("Intro Music Bed (.mp3):", self.create_file_selector(self.intro_path))
        music_layout.addRow("Ender Audio (.mp3):", self.create_file_selector(self.ender_path))
        music_group_box.setLayout(music_layout)
        main_layout.addWidget(music_group_box)

        self.generate_button = QPushButton("Generate Skit")
        self.generate_button.clicked.connect(self.generate_skit)
        main_layout.addWidget(self.generate_button)

        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)

        self.load_config()
        
        # Disable button if any voice files are missing
        if any(char not in self.voice_transcripts for char in CHARACTERS):
             self.generate_button.setEnabled(False)
             self.generate_button.setText("Voice file(s) missing, check console.")

    def manage_voice_transcripts(self):
        """
        Analyzes voice files on startup, transcribes if new or updated,
        and caches the results for future sessions.
        """
        print("--- Checking Voice File Transcripts ---")
        self.initialize_f5_model()
        
        cache = {}
        if os.path.exists(TRANSCRIPT_CACHE_FILE):
            with open(TRANSCRIPT_CACHE_FILE, 'r') as f:
                cache = json.load(f)

        updated_cache = {}
        needs_update = False

        for char, file_path in VOICE_FILES.items():
            if not os.path.exists(file_path):
                print(f"ERROR: Voice file for {char} not found at '{file_path}'.")
                continue

            current_mod_time = os.path.getmtime(file_path)
            cached_entry = cache.get(file_path)

            if cached_entry and cached_entry.get("mod_time") == current_mod_time:
                print(f"  -> '{char}' transcript loaded from cache.")
                transcript = cached_entry["transcript"]
            else:
                print(f"  -> Analyzing new/updated voice file for '{char}'...")
                QApplication.processEvents() # Allow UI to update
                try:
                    transcript = self.f5_model.transcribe(file_path)
                    print(f"     ...Done. Transcript: '{transcript}'")
                    needs_update = True
                except Exception as e:
                    print(f"ERROR: Could not transcribe {file_path}. Error: {e}")
                    self.show_message("Transcription Error", f"Could not analyze the voice file for {char}.\nPlease check the file and restart.")
                    continue

            self.voice_transcripts[char] = transcript
            updated_cache[file_path] = {"transcript": transcript, "mod_time": current_mod_time}
        
        if needs_update:
            print(f"--- Saving updated transcripts to '{TRANSCRIPT_CACHE_FILE}' ---")
            with open(TRANSCRIPT_CACHE_FILE, 'w') as f:
                json.dump(updated_cache, f, indent=4)

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
        if file_path:
            line_edit_widget.setText(file_path)

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

    def generate_skit(self):
        # --- 1. Validate Inputs ---
        character_data = {}
        for char, widgets in self.character_widgets.items():
            speed = widgets["speed"].text()
            seed = widgets["seed"].text()
            try:
                character_data[char] = {
                    "ref_audio": VOICE_FILES[char],
                    "ref_text": self.voice_transcripts[char],
                    "speed": float(speed),
                    "seed": int(seed)
                }
            except (ValueError, KeyError) as e:
                self.show_message("Invalid Input", f"The settings for {char} are invalid. Please check them. Error: {e}")
                return

        intro_file = self.intro_path.text()
        ender_file = self.ender_path.text()
        if not all([intro_file, ender_file]):
            self.show_message("Missing Information", "Please select an Intro Music Bed and an Ender Audio file.")
            return

        self.progress_bar.setValue(0)
        self.generate_button.setEnabled(False)
        QApplication.processEvents()
        
        if not self.initialize_f5_model():
             self.generate_button.setEnabled(True)
             return

        # --- 2. Generate AI Script ---
        dialogue = self.generate_seinfeld_skit()
        if not dialogue:
            self.show_message("Error", "Failed to generate dialogue from AI.")
            self.generate_button.setEnabled(True)
            return
        
        self.progress_bar.setValue(25)
        # (The rest of the generation logic remains largely the same...)
        # --- 3. Generate Audio for each Line ---
        print("Generating audio for each line using F5 TTS...")
        dialogue_segments = []
        temp_files_to_clean = []
        total_lines = len(dialogue)
        for i, line in enumerate(dialogue):
            speaker = line["speaker"]
            text = line["line"]
            
            if speaker not in character_data:
                print(f"Warning: Script generated a line for an unknown speaker '{speaker}'. Skipping.")
                continue

            speaker_info = character_data[speaker]
            print(f"  -> Generating for {speaker}: '{text}'")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_f:
                temp_line_file = temp_f.name
                temp_files_to_clean.append(temp_line_file)
            
            try:
                self.f5_model.infer(
                    ref_file=speaker_info["ref_audio"],
                    ref_text=speaker_info["ref_text"],
                    gen_text=text,
                    speed=speaker_info["speed"],
                    file_wave=temp_line_file,
                    seed=speaker_info["seed"]
                )
                segment = AudioSegment.from_wav(temp_line_file)
                dialogue_segments.append(segment)
            except Exception as e:
                self.show_message("TTS Error", f"Failed to generate audio for {speaker}:\n'{text}'\nError: {e}")
                self.generate_button.setEnabled(True)
                for f in temp_files_to_clean: os.remove(f)
                return

            progress = 25 + int(60 * (i + 1) / total_lines)
            self.progress_bar.setValue(progress)
            QApplication.processEvents()

        # --- 4. Assemble Final Audio ---
        print("Assembling final audio segment...")
        try:
            intro_music_bed = AudioSegment.from_mp3(intro_file)
            ender_segment = AudioSegment.from_mp3(ender_file)
            pause = AudioSegment.silent(duration=300)

            full_dialogue = AudioSegment.empty()
            for seg in dialogue_segments:
                full_dialogue += seg + pause

            music_bed_trimmed = intro_music_bed[:len(full_dialogue)]
            ducked_bed = music_bed_trimmed - 6
            dialogue_on_bed = ducked_bed.overlay(full_dialogue)
            
            final_segment = dialogue_on_bed + ender_segment
            self.progress_bar.setValue(95)

            # --- 5. Export Final MP3 ---
            save_path, _ = QFileDialog.getSaveFileName(self, "Save Skit As...", "", "MP3 Files (*.mp3)")
            if save_path:
                if not save_path.endswith('.mp3'):
                    save_path += '.mp3'
                
                final_segment.export(save_path, format="mp3")
                self.progress_bar.setValue(100)
                self.show_message("Success", f"Skit saved successfully to:\n{save_path}")
            else:
                self.progress_bar.setValue(0)

        except Exception as e:
            self.show_message("Audio Assembly Error", f"Failed to assemble the final audio: {e}")
            self.progress_bar.setValue(0)
        finally:
            print("Cleaning up temporary files...")
            for f in temp_files_to_clean:
                try: os.remove(f)
                except OSError as e: print(f"Error removing temp file {f}: {e}")
            self.generate_button.setEnabled(True)

    def generate_seinfeld_skit(self):
        try:
            prompt = (
                "You are a scriptwriter for a short, Seinfeld-style comedy radio play. The scene is set in Monk's coffee shop.\n"
                "The script must feature only the following four characters: JERRY, GEORGE, ELAINE, and KRAMER.\n"
                "Create a short, witty dialogue of about 6-8 lines total that builds to a quick, funny payoff.\n"
                "IMPORTANT: The script must contain only dialogue. Do not include any stage directions, actions, or parenthetical character direction.\n"
                "Format the script EXACTLY like this:\n"
                "JERRY: [First line of dialogue]\nGEORGE: [Second line of dialogue]\n..."
            )
            response = ollama.chat(model=MODEL_TO_USE, messages=[{'role': 'user', 'content': prompt}])
            full_script = response['message']['content'].strip()
            print(f"--- Generated Script ---\n{full_script}\n------------------------")
            parsed_dialogue = []
            for line in full_script.split('\n'):
                match = re.match(r'^(' + '|'.join(CHARACTERS) + r'):\s*(.*)', line.strip())
                if match:
                    parsed_dialogue.append({"speaker": match.group(1), "line": match.group(2).strip()})
            return parsed_dialogue
        except Exception as e:
            print(f"Failed to generate script from Ollama: {e}")
            return None

    def show_message(self, title, message):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.exec()

    def save_config(self):
        config_data = {
            "characters": {},
            "audio_production": {
                "intro_file": self.intro_path.text(),
                "ender_file": self.ender_path.text()
            }
        }
        for char, widgets in self.character_widgets.items():
            config_data["characters"][char] = {
                "speed": widgets["speed"].text(),
                "seed": widgets["seed"].text()
            }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            print(f"Error saving config file: {e}")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config_data = json.load(f)
                
                char_data = config_data.get("characters", {})
                for char, widgets in self.character_widgets.items():
                    data = char_data.get(char, {})
                    widgets["speed"].setText(data.get("speed", "1.0"))
                    widgets["seed"].setText(data.get("seed", "-1"))
                
                prod_data = config_data.get("audio_production", {})
                self.intro_path.setText(prod_data.get("intro_file", ""))
                self.ender_path.setText(prod_data.get("ender_file", ""))
            except Exception as e:
                print(f"Error loading config file: {e}")

    def closeEvent(self, event):
        self.save_config()
        event.accept()

if __name__ == "__main__":
    if F5TTS is None:
        sys.exit(1)

    app = QApplication(sys.argv)
    creator = SkitCreator()
    creator.show()
    sys.exit(app.exec())