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
    QProgressBar, QFormLayout, QGroupBox, QComboBox, QTextEdit
)
from pydub import AudioSegment
from pydub.effects import normalize
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
OUTPUT_FOLDER = "skits"

# --- Hardcoded Asset Paths ---
VOICE_FILES = {
    "JERRY": "voices/jerry.wav",
    "GEORGE": "voices/george.wav",
    "ELAINE": "voices/elaine.wav",
    "KRAMER": "voices/kramer.wav"
}
LAUGH_FILES = {
    "SHORT": "laughs/laugh_short.mp3",
    "LONG": "laughs/laugh_long.mp3"
}
CHARACTERS = list(VOICE_FILES.keys())

class SkitCreator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Direct Sound Radio - Skit Creator")
        self.setGeometry(100, 100, 750, 860)

        self.f5_model = None
        self.voice_transcripts = {}
        self.ollama_models = []
        self.skit_prompt = ""
        self.laugh_tracks = {}

        self.load_or_create_prompt_config()
        if F5TTS:
            self.manage_voice_transcripts()
        
        self.load_laugh_tracks()

        # --- UI Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        model_group_box = QGroupBox("AI Model Selection")
        model_layout = QFormLayout()
        self.model_selector = QComboBox()
        self.batch_selector = QComboBox()
        self.batch_selector.addItems(["1", "3", "5", "10"])
        self.populate_ollama_models()
        model_layout.addRow("Generator Model:", self.model_selector)
        model_layout.addRow("Number of Skits:", self.batch_selector)
        model_group_box.setLayout(model_layout)
        main_layout.addWidget(model_group_box)

        # --- Topic Input UI ---
        topic_group_box = QGroupBox("Skit Topic (Optional)")
        topic_layout = QVBoxLayout()
        self.topic_input = QTextEdit()
        self.topic_input.setPlaceholderText("Leave blank for a random topic, or enter a specific subject, URL, etc.")
        self.topic_input.setMaximumHeight(80)
        topic_layout.addWidget(self.topic_input)
        topic_group_box.setLayout(topic_layout)
        main_layout.addWidget(topic_group_box)

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
        
        # --- Startup validation checks ---
        if any(char not in self.voice_transcripts for char in CHARACTERS):
             self.generate_button.setEnabled(False)
             self.generate_button.setText("Voice file(s) missing, check console.")
        
        if self.model_selector.count() == 0:
            self.generate_button.setEnabled(False)
            self.generate_button.setText("Ollama not running or no models found.")
            
        if not self.laugh_tracks:
            self.generate_button.setEnabled(False)
            self.generate_button.setText("Laugh track file(s) missing, check console.")

    def load_laugh_tracks(self):
        print("--- Loading Laugh Tracks ---")
        for laugh_type, path in LAUGH_FILES.items():
            if os.path.exists(path):
                try:
                    self.laugh_tracks[laugh_type] = AudioSegment.from_mp3(path)
                    print(f"  -> Loaded '{path}' successfully.")
                except Exception as e:
                    print(f"ERROR: Could not load laugh track '{path}': {e}")
                    self.laugh_tracks = {}
                    break
            else:
                print(f"ERROR: Laugh track file not found at '{path}'.")
                self.laugh_tracks = {}
                break

    def load_or_create_prompt_config(self):
        default_prompt = (
            'You are an expert scriptwriter for a "Seinfeld"-style comedy radio play...'
        )
        try:
            with open(PROMPT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.skit_prompt = config.get("seinfeld_skit_prompt", default_prompt)
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"--- No valid prompt config found. Creating '{PROMPT_CONFIG_FILE}'. ---")
            self.skit_prompt = default_prompt
            with open(PROMPT_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"seinfeld_skit_prompt": default_prompt}, f, indent=4)

    def populate_ollama_models(self):
        try:
            response = requests.get('http://127.0.0.1:11434/api/tags')
            response.raise_for_status() 
            models_data = response.json()['models']
            self.ollama_models = [model['name'] for model in models_data]
            if self.ollama_models:
                self.model_selector.addItems(self.ollama_models)
        except requests.exceptions.ConnectionError:
            self.show_message("Ollama Connection Error", "Could not connect to the Ollama server.")
        except Exception as e:
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
                    needs_update = True
                except Exception as e:
                    self.show_message("Transcription Error", f"Could not analyze voice file for {char}.")
                    continue
            self.voice_transcripts[char] = transcript
            updated_cache[file_path] = {"transcript": transcript, "mod_time": current_mod_time}
        
        if needs_update:
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
            try:
                self.f5_model = F5TTS()
            except Exception as e:
                self.show_message("F5 TTS Error", f"Could not initialize the F5 TTS model: {e}")
                return False
        return True

    def generate_skit_batch(self):
        character_data = {}
        for char, widgets in self.character_widgets.items():
            try:
                character_data[char] = {
                    "ref_audio": VOICE_FILES[char], "ref_text": self.voice_transcripts[char],
                    "speed": float(widgets["speed"].text()), "seed": int(widgets["seed"].text())
                }
            except (ValueError, KeyError) as e:
                self.show_message("Invalid Input", f"The settings for {char} are invalid.")
                return

        try:
            dialogue_delay_sec = float(self.dialogue_delay_widget.text())
        except ValueError:
            self.show_message("Invalid Input", "Dialogue Delay must be a number.")
            return

        intro_file, ender_file = self.intro_path.text(), self.ender_path.text()
        if not all([intro_file, ender_file]):
            self.show_message("Missing Information", "Please select an Intro and Ender file.")
            return

        batch_count = int(self.batch_selector.currentText())
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        
        self.progress_bar.setValue(0)
        self.generate_button.setEnabled(False)
        QApplication.processEvents()
        
        if not self.initialize_f5_model():
             self.generate_button.setEnabled(True)
             return

        for i in range(batch_count):
            current_batch_num = i + 1
            print(f"\n--- Generating Skit {current_batch_num} of {batch_count} ---")
            
            parsed_script = self.generate_seinfeld_skit()
            if not parsed_script:
                self.show_message("Error", f"Failed to generate dialogue for skit {current_batch_num}.")
                break 
            
            self.progress_bar.setValue(int(((i + 0.25) / batch_count) * 100))

            audio_events = []
            temp_files_to_clean = []
            generation_successful = True
            for script_item in parsed_script:
                if script_item['type'] == 'dialogue':
                    speaker = script_item['speaker']
                    text = script_item['line']
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_f:
                        temp_line_file = temp_f.name
                        temp_files_to_clean.append(temp_line_file)
                    try:
                        self.f5_model.infer(
                            ref_file=character_data[speaker]["ref_audio"], ref_text=character_data[speaker]["ref_text"],
                            gen_text=text, speed=character_data[speaker]["speed"],
                            file_wave=temp_line_file, seed=character_data[speaker]["seed"]
                        )
                        audio_events.append({'type': 'dialogue', 'segment': AudioSegment.from_wav(temp_line_file)})
                    except Exception as e:
                        self.show_message("TTS Error", f"Failed TTS for {speaker}: {e}")
                        generation_successful = False
                        break
                elif script_item['type'] == 'laugh':
                    laugh_type = script_item['duration'].upper()
                    if laugh_type in self.laugh_tracks:
                        audio_events.append({'type': 'laugh', 'segment': self.laugh_tracks[laugh_type]})
            
            if not generation_successful:
                for f in temp_files_to_clean:
                    try: os.remove(f)
                    except OSError: pass
                continue

            self.progress_bar.setValue(int(((i + 0.85) / batch_count) * 100))

            try:
                print(f"Assembling audio for skit {current_batch_num}...")
                intro_music_bed = AudioSegment.from_mp3(intro_file)
                ender_segment = AudioSegment.from_mp3(ender_file)
                pause = AudioSegment.silent(duration=300)

                # First, build a timeline of events with their start times
                timeline = []
                current_time = 0
                for event in audio_events:
                    timeline.append({'event': event, 'start_time': current_time})
                    if event['type'] == 'dialogue':
                        current_time += len(event['segment']) + len(pause)

                # Determine the total length needed for the main track
                total_duration = current_time
                dialogue_track = AudioSegment.silent(duration=total_duration)

                # Now, place (overlay) all events onto the silent track
                for item in timeline:
                    event = item['event']
                    start_time = item['start_time']
                    if event['type'] == 'dialogue':
                        dialogue_track = dialogue_track.overlay(event['segment'], position=start_time)
                    elif event['type'] == 'laugh':
                        # Laughs are placed at the start time of the dialogue line they follow
                        quiet_laugh = event['segment'] - 15 # Quieter laugh
                        dialogue_track = dialogue_track.overlay(quiet_laugh, position=start_time)

                initial_delay = AudioSegment.silent(duration=int(dialogue_delay_sec * 1000))
                full_dialogue = initial_delay + dialogue_track

                music_bed_trimmed = intro_music_bed[:len(full_dialogue)]
                dialogue_on_bed = (music_bed_trimmed - 6).overlay(full_dialogue)
                final_segment = dialogue_on_bed + ender_segment

                normalized_segment = normalize(final_segment)

                timestamp = int(time.time())
                save_path = os.path.join(OUTPUT_FOLDER, f"skit_{timestamp}_{current_batch_num}.mp3")
                
                normalized_segment.export(save_path, format="mp3")
            except Exception as e:
                self.show_message("Audio Assembly Error", f"Failed to assemble skit {current_batch_num}: {e}")
            finally:
                for f in temp_files_to_clean:
                    try: os.remove(f)
                    except OSError: pass
            
            self.progress_bar.setValue(int((current_batch_num / batch_count) * 100))

        self.generate_button.setEnabled(True)
        self.show_message("Success", f"Batch completed. Saved to '{OUTPUT_FOLDER}' folder.")

    def generate_seinfeld_skit(self):
        selected_model = self.model_selector.currentText()
        if not selected_model: return None
        try:
            base_prompt = self.skit_prompt
            user_topic = self.topic_input.toPlainText().strip()
            
            final_prompt = base_prompt
            if user_topic:
                final_prompt += f"\n\nIMPORTANT ADDITIONAL CONTEXT: The dialogue MUST revolve around the following topic, website, or idea provided by the user:\n{user_topic}"

            response = ollama.chat(model=selected_model, messages=[{'role': 'user', 'content': final_prompt}])
            full_script = response['message']['content'].strip()
            print(f"--- Generated Script ---\n{full_script}\n------------------------")
            
            parsed_script = []
            for line in full_script.split('\n'):
                line = line.strip()
                if not line: continue

                dialogue_match = re.match(r'^(' + '|'.join(CHARACTERS) + r'):\s*(.*)', line)
                
                if dialogue_match:
                    speaker = dialogue_match.group(1)
                    content = dialogue_match.group(2).strip()
                    
                    laugh_short_match = re.search(r'\[LAUGH_SHORT\]', content, re.IGNORECASE)
                    laugh_long_match = re.search(r'\[LAUGH_LONG\]', content, re.IGNORECASE)
                    
                    text_to_speak = re.sub(r'\s*\([^)]*\)\s*', ' ', content)
                    text_to_speak = re.sub(r'\s*\[LAUGH_(?:SHORT|LONG)\]\s*', ' ', text_to_speak, flags=re.IGNORECASE).strip()
                    
                    if text_to_speak:
                        parsed_script.append({"type": "dialogue", "speaker": speaker, "line": text_to_speak})
                    
                    if laugh_long_match:
                        parsed_script.append({"type": "laugh", "duration": "long"})
                    elif laugh_short_match:
                        parsed_script.append({"type": "laugh", "duration": "short"})
                
                elif re.match(r'^\s*\[LAUGH_LONG\]\s*$', line, re.IGNORECASE):
                    parsed_script.append({"type": "laugh", "duration": "long"})
                elif re.match(r'^\s*\[LAUGH_SHORT\]\s*$', line, re.IGNORECASE):
                    parsed_script.append({"type": "laugh", "duration": "short"})

            print("--- Parsed Script Events ---")
            for item in parsed_script:
                detail = item.get('speaker') or item.get('duration')
                print(f"  - Type: {item['type']:<10} | Detail: {detail}")
            print("----------------------------")

            return parsed_script
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
            "batch_count": self.batch_selector.currentText(),
            "topic": self.topic_input.toPlainText(),
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
                
                if saved_model := config_data.get("ai_model"): self.model_selector.setCurrentText(saved_model)
                if saved_batch_count := config_data.get("batch_count"): self.batch_selector.setCurrentText(saved_batch_count)
                if topic_text := config_data.get("topic"): self.topic_input.setPlainText(topic_text)

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

