import sys
import os
import re
import tempfile
import json
import time
import requests
import itertools
import random
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QLabel, QLineEdit,
    QProgressBar, QFormLayout, QGroupBox, QComboBox, QTextEdit, QTabWidget
)
from pydub import AudioSegment
from pydub.effects import normalize
import ollama

# --- F5 TTS Integration ---
try:
    from api import F5TTS
except ImportError:
    print("FATAL: Could not import F5TTS from api.py.")
    F5TTS = None

# --- Configuration ---
CONFIG_FILE = "segment_producer_config.json"
TRANSCRIPT_CACHE_FILE = "transcript_cache.json"
PROMPT_CONFIG_FILE = "prompt_config.json"
OUTPUT_FOLDER = "skits"
LAUGHS_FOLDER = "laughs"

class SegmentProducer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Direct Sound Radio - Segment Producer")
        self.setGeometry(100, 100, 750, 950)

        self.f5_model = None
        self.ollama_models = []
        self.base_prompt_template = ""
        self.laugh_tracks = []
        self.laugh_cycle = None

        self.load_or_create_prompt_config()
        if F5TTS:
            # F5 model is initialized on demand later
            pass
        
        self.load_laugh_tracks()

        # --- UI Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Main Tab Widget ---
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # --- AI Generator Tab ---
        ai_generator_tab = QWidget()
        ai_layout = QVBoxLayout(ai_generator_tab)
        
        model_group_box = QGroupBox("AI Configuration")
        model_layout = QFormLayout()
        self.model_selector = QComboBox()
        self.batch_selector = QComboBox()
        self.batch_selector.addItems(["1", "3", "5", "10"])
        self.populate_ollama_models()
        model_layout.addRow("Generator Model:", self.model_selector)
        model_layout.addRow("Number of Segments:", self.batch_selector)
        model_group_box.setLayout(model_layout)
        ai_layout.addWidget(model_group_box)

        topic_group_box = QGroupBox("AI Topic (Optional if script is provided)")
        topic_layout = QVBoxLayout()
        self.topic_input = QTextEdit()
        self.topic_input.setPlaceholderText("Leave blank for a random topic, or enter a specific subject, URL, etc.")
        self.topic_input.setMaximumHeight(80)
        topic_layout.addWidget(self.topic_input)
        topic_group_box.setLayout(topic_layout)
        ai_layout.addWidget(topic_group_box)
        ai_layout.addStretch()

        # --- Manual Script Tab ---
        self.manual_script_tab = QWidget()
        manual_layout = QVBoxLayout(self.manual_script_tab)
        
        manual_group_box = QGroupBox("Paste Your Script Here")
        manual_box_layout = QVBoxLayout()
        instruction_label = QLabel("Use the exact Speaker Names defined below. Use [LAUGH] for laughs.")
        instruction_label.setWordWrap(True)
        self.manual_script_input = QTextEdit()
        manual_box_layout.addWidget(instruction_label)
        manual_box_layout.addWidget(self.manual_script_input)
        manual_group_box.setLayout(manual_box_layout)
        manual_layout.addWidget(manual_group_box)

        self.tabs.addTab(ai_generator_tab, "AI Generator")
        self.tabs.addTab(self.manual_script_tab, "Manual Script")

        # --- Speaker Configuration ---
        speaker_config_box = QGroupBox("Speaker Configuration")
        speaker_config_layout = QVBoxLayout(speaker_config_box)
        
        num_speakers_layout = QHBoxLayout()
        num_speakers_layout.addWidget(QLabel("Number of Speakers:"))
        self.num_speakers_selector = QComboBox()
        self.num_speakers_selector.addItems([str(i) for i in range(1, 5)])
        self.num_speakers_selector.currentIndexChanged.connect(self._update_speaker_widgets)
        num_speakers_layout.addWidget(self.num_speakers_selector)
        num_speakers_layout.addStretch()
        speaker_config_layout.addLayout(num_speakers_layout)

        self.speaker_widgets = []
        for i in range(4):
            group = QGroupBox(f"Speaker {i+1}")
            layout = QFormLayout(group)
            
            name_edit = QLineEdit()
            ref_audio_edit = QLineEdit()
            ref_transcript_edit = QLineEdit()
            speed_edit = QLineEdit("1.0")
            seed_edit = QLineEdit("-1")

            layout.addRow("Speaker Name:", name_edit)
            layout.addRow("Reference Voice (.wav):", self.create_file_selector(ref_audio_edit))
            layout.addRow("Reference Transcript:", ref_transcript_edit)
            layout.addRow("Speech Speed:", speed_edit)
            layout.addRow("Voice Seed:", seed_edit)
            
            speaker_config_layout.addWidget(group)
            self.speaker_widgets.append({
                "group": group, "name": name_edit, "ref_audio": ref_audio_edit,
                "ref_transcript": ref_transcript_edit, "speed": speed_edit, "seed": seed_edit
            })
        
        main_layout.addWidget(speaker_config_box)
        
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

        self.generate_button = QPushButton("Generate Segment(s)")
        self.generate_button.clicked.connect(self.generate_skit_batch)
        main_layout.addWidget(self.generate_button)

        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)

        self.load_config()
        self._update_speaker_widgets() # Set initial visibility

    def _update_speaker_widgets(self):
        num_speakers = int(self.num_speakers_selector.currentText())
        for i, widget_dict in enumerate(self.speaker_widgets):
            widget_dict["group"].setVisible(i < num_speakers)

    def load_laugh_tracks(self):
        print("--- Loading Laugh Tracks ---")
        self.laugh_tracks = []
        if not os.path.exists(LAUGHS_FOLDER):
            os.makedirs(LAUGHS_FOLDER)
            print(f"INFO: Laughs folder created at '{LAUGHS_FOLDER}'. Please add laugh track .mp3 files here.")
            return

        for filename in sorted(os.listdir(LAUGHS_FOLDER)):
            if filename.lower().endswith('.mp3'):
                path = os.path.join(LAUGHS_FOLDER, filename)
                try:
                    self.laugh_tracks.append(AudioSegment.from_mp3(path))
                    print(f"  -> Loaded '{path}' successfully.")
                except Exception as e:
                    print(f"ERROR: Could not load laugh track '{path}': {e}")
        
        if self.laugh_tracks:
            random.shuffle(self.laugh_tracks)
            self.laugh_cycle = itertools.cycle(self.laugh_tracks)
        else:
            print("WARNING: No valid laugh tracks found in the laughs folder.")

    def load_or_create_prompt_config(self):
        default_prompt = (
            "You are a scriptwriter for a radio show. Write a script based on the provided topic for the following characters: {characters}.\n\n"
            "The dialogue should be conversational and engaging. Based on the comedic timing, insert a [LAUGH] cue on its own line or at the end of a character's line where a laugh should occur.\n\n"
            "Format the script EXACTLY like this, using the provided character names in ALL CAPS:\n"
            "CHARACTER_NAME_1: [First line of dialogue]\n"
            "CHARACTER_NAME_2: [Second line of dialogue] [LAUGH]\n"
        )
        try:
            with open(PROMPT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.base_prompt_template = config.get("prompt_template", default_prompt)
        except (FileNotFoundError, json.JSONDecodeError):
            self.base_prompt_template = default_prompt
            with open(PROMPT_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"prompt_template": default_prompt}, f, indent=4)

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
                print("--- Initializing F5 TTS Engine (this may take a moment) ---")
                self.f5_model = F5TTS()
                print("--- F5 TTS Engine loaded successfully. ---")
            except Exception as e:
                self.show_message("F5 TTS Error", f"Could not initialize the F5 TTS model: {e}")
                return False
        return True

    def generate_skit_batch(self):
        # --- 1. Gather settings and validate ---
        num_speakers = int(self.num_speakers_selector.currentText())
        character_data = {}
        active_characters = []
        for i in range(num_speakers):
            widgets = self.speaker_widgets[i]
            name = widgets["name"].text().strip().upper()
            if not name:
                self.show_message("Invalid Input", f"Speaker {i+1} must have a name.")
                return
            
            ref_audio = widgets["ref_audio"].text()
            ref_transcript = widgets["ref_transcript"].text()

            if not all([ref_audio, ref_transcript]):
                self.show_message("Invalid Input", f"Speaker '{name}' is missing a reference voice or transcript.")
                return

            try:
                character_data[name] = {
                    "ref_audio": ref_audio, "ref_transcript": ref_transcript,
                    "speed": float(widgets["speed"].text()), "seed": int(widgets["seed"].text())
                }
                active_characters.append(name)
            except (ValueError, KeyError) as e:
                self.show_message("Invalid Input", f"The settings for speaker '{name}' are invalid.")
                return
        
        intro_file, ender_file = self.intro_path.text(), self.ender_path.text()
        if not all([intro_file, ender_file]):
            self.show_message("Missing Information", "Please select an Intro and Ender file.")
            return

        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        self.generate_button.setEnabled(False)
        self.progress_bar.setValue(0)
        QApplication.processEvents()
        
        if not self.initialize_f5_model():
             self.generate_button.setEnabled(True)
             return

        # --- 2. Determine script source and get scripts to process ---
        scripts_to_process = []
        is_manual_mode = self.tabs.currentWidget() == self.manual_script_tab
        manual_script_text = self.manual_script_input.toPlainText().strip()

        if is_manual_mode and manual_script_text:
            print("\n--- Using Manual Script Input ---")
            parsed_script = self.parse_raw_script(manual_script_text, active_characters)
            if parsed_script: scripts_to_process.append(parsed_script)
        else: # AI Mode
            batch_count = int(self.batch_selector.currentText())
            for i in range(batch_count):
                print(f"\n--- Generating AI Skit {i + 1} of {batch_count} ---")
                parsed_script = self.generate_ai_script(active_characters)
                if parsed_script: scripts_to_process.append(parsed_script)
                else:
                    self.show_message("Error", f"Failed to generate dialogue for skit {i + 1}.")
                    break 

        # --- 3. Process each script in the list ---
        total_skits = len(scripts_to_process)
        for i, parsed_script in enumerate(scripts_to_process):
            current_batch_num = i + 1
            
            self.progress_bar.setValue(int(((i + 0.25) / total_skits) * 100))
            audio_events, temp_files_to_clean, gen_success = self.generate_audio_events(parsed_script, character_data)
            
            if not gen_success:
                for f in temp_files_to_clean:
                    try: os.remove(f)
                    except OSError: pass
                continue

            self.progress_bar.setValue(int(((i + 0.85) / total_skits) * 100))
            try:
                self.assemble_and_save_audio(current_batch_num, audio_events, intro_file, ender_file, float(self.dialogue_delay_widget.text()))
            finally:
                for f in temp_files_to_clean:
                    try: os.remove(f)
                    except OSError: pass
            
            self.progress_bar.setValue(int((current_batch_num / total_skits) * 100))

        self.generate_button.setEnabled(True)
        self.show_message("Success", f"Batch completed. Saved {len(scripts_to_process)} segment(s) to the '{OUTPUT_FOLDER}' folder.")

    def generate_audio_events(self, parsed_script, character_data):
        audio_events, temp_files, is_successful = [], [], True
        for item in parsed_script:
            if item['type'] == 'dialogue':
                speaker, text = item['speaker'], item['line']
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_f:
                    temp_line_file = temp_f.name
                    temp_files.append(temp_line_file)
                try:
                    self.f5_model.infer(
                        ref_file=character_data[speaker]["ref_audio"], ref_text=character_data[speaker]["ref_transcript"],
                        gen_text=text, speed=character_data[speaker]["speed"],
                        file_wave=temp_line_file, seed=character_data[speaker]["seed"]
                    )
                    audio_events.append({'type': 'dialogue', 'segment': AudioSegment.from_wav(temp_line_file)})
                except Exception as e:
                    self.show_message("TTS Error", f"Failed TTS for {speaker}: {e}")
                    is_successful = False
                    break
            elif item['type'] == 'laugh' and self.laugh_cycle:
                audio_events.append({'type': 'laugh', 'segment': next(self.laugh_cycle)})
        return audio_events, temp_files, is_successful

    def assemble_and_save_audio(self, skit_num, audio_events, intro_file, ender_file, delay_sec):
        try:
            print(f"Assembling audio for skit {skit_num}...")
            intro_music_bed = AudioSegment.from_mp3(intro_file)
            ender_segment = AudioSegment.from_mp3(ender_file)
            pause = AudioSegment.silent(duration=300)

            dialogue_only_track = AudioSegment.empty()
            laugh_timestamps = []
            for event in audio_events:
                if event['type'] == 'dialogue':
                    dialogue_only_track += event['segment'] + pause
                elif event['type'] == 'laugh':
                    laugh_timestamps.append(len(dialogue_only_track))

            final_dialogue_track = dialogue_only_track
            for timestamp in laugh_timestamps:
                if self.laugh_cycle:
                    laugh_segment = next(self.laugh_cycle)
                    quiet_laugh = laugh_segment - 4
                    final_dialogue_track = final_dialogue_track.overlay(quiet_laugh, position=timestamp)

            initial_delay = AudioSegment.silent(duration=int(delay_sec * 1000))
            full_dialogue = initial_delay + final_dialogue_track

            music_bed_trimmed = intro_music_bed[:len(full_dialogue)]
            dialogue_on_bed = (music_bed_trimmed - 6).overlay(full_dialogue)
            final_segment = dialogue_on_bed + ender_segment

            normalized_segment = normalize(final_segment)

            timestamp = int(time.time())
            save_path = os.path.join(OUTPUT_FOLDER, f"segment_{timestamp}_{skit_num}.mp3")
            
            normalized_segment.export(save_path, format="mp3")
        except Exception as e:
            self.show_message("Audio Assembly Error", f"Failed to assemble skit {skit_num}: {e}")

    def parse_raw_script(self, raw_text, active_characters):
        print("--- Parsing Raw Script ---")
        return self._parse_script_lines(raw_text.split('\n'), active_characters)

    def generate_ai_script(self, active_characters):
        selected_model = self.model_selector.currentText()
        if not selected_model: return None
        try:
            user_topic = self.topic_input.toPlainText().strip()
            
            final_prompt = self.base_prompt_template.format(characters=", ".join(active_characters))
            if user_topic:
                final_prompt += f"\n\nIMPORTANT: The dialogue MUST revolve around the following topic:\n{user_topic}"
            else:
                final_prompt += "\n\nThe topic can be anything you imagine."

            response = ollama.chat(model=selected_model, messages=[{'role': 'user', 'content': final_prompt}])
            full_script = response['message']['content'].strip()
            print(f"--- Generated Script ---\n{full_script}\n------------------------")
            
            return self._parse_script_lines(full_script.split('\n'), active_characters)
        except Exception as e:
            print(f"Failed to generate script: {e}")
            return None

    def _parse_script_lines(self, lines, active_characters):
        parsed_script = []
        character_regex = '|'.join(re.escape(c) for c in active_characters)
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            parts = re.split(r'(\[LAUGH\])', line, flags=re.IGNORECASE)
            
            current_speaker = None
            dialogue_match = re.match(r'^(' + character_regex + r'):\s*(.*)', parts[0])
            if dialogue_match:
                current_speaker = dialogue_match.group(1)
                parts[0] = dialogue_match.group(2)

            for part in parts:
                part = part.strip()
                if not part: continue

                if part.upper() == '[LAUGH]':
                    parsed_script.append({"type": "laugh"})
                elif current_speaker:
                    text_to_speak = re.sub(r'\s*\([^)]*\)\s*', ' ', part).strip()
                    if text_to_speak:
                        parsed_script.append({
                            "type": "dialogue", 
                            "speaker": current_speaker, 
                            "line": text_to_speak
                        })

        print("--- Parsed Script Events ---")
        for item in parsed_script:
            detail = item.get('line') or 'N/A'
            print(f"  - Type: {item['type']:<10} | Detail: {detail}")
        print("----------------------------")

        return parsed_script

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
            "num_speakers": self.num_speakers_selector.currentText(),
            "speakers": [],
            "audio_production": {
                "intro_file": self.intro_path.text(), "ender_file": self.ender_path.text(),
                "dialogue_delay": self.dialogue_delay_widget.text()
            }
        }
        for widgets in self.speaker_widgets:
            config_data["speakers"].append({
                "name": widgets["name"].text(),
                "ref_audio": widgets["ref_audio"].text(),
                "ref_transcript": widgets["ref_transcript"].text(),
                "speed": widgets["speed"].text(),
                "seed": widgets["seed"].text()
            })
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)
        except Exception as e: print(f"Error saving config file: {e}")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f: config_data = json.load(f)
                
                if saved_model := config_data.get("ai_model"): self.model_selector.setCurrentText(saved_model)
                if saved_batch := config_data.get("batch_count"): self.batch_selector.setCurrentText(saved_batch)
                if topic := config_data.get("topic"): self.topic_input.setPlainText(topic)
                if num_speakers := config_data.get("num_speakers"): self.num_speakers_selector.setCurrentText(num_speakers)

                speaker_data = config_data.get("speakers", [])
                for i, data in enumerate(speaker_data):
                    if i < len(self.speaker_widgets):
                        widgets = self.speaker_widgets[i]
                        widgets["name"].setText(data.get("name", ""))
                        widgets["ref_audio"].setText(data.get("ref_audio", ""))
                        widgets["ref_transcript"].setText(data.get("ref_transcript", ""))
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
    producer = SegmentProducer()
    producer.show()
    sys.exit(app.exec())

