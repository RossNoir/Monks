#!/usr/bin/env python3
"""
StemSplitter Pro - Personal Music Separation App
===============================================

A desktop application for separating audio tracks into stems (vocals, bass, drums, other)
and creating custom mixes by muting specific tracks. Perfect for music practice and karaoke!

Requirements:
- pip install demucs torch torchaudio librosa soundfile tkinter-tooltip
- For GPU acceleration: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

Author: Claude & User
Version: 1.0
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import sys
from pathlib import Path
import subprocess
import json
from datetime import datetime
import queue
import time

# Third-party imports (will be installed)
try:
    import torch
    import demucs.api
    import librosa
    import soundfile as sf
    import numpy as np
    from tktooltip import ToolTip
except ImportError as e:
    print(f"Missing required packages. Please run:")
    print("pip install demucs torch torchaudio librosa soundfile tkinter-tooltip")
    print(f"Error: {e}")
    sys.exit(1)


class StemSplitterApp:
    """Main application class for the Stem Splitter Pro"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("🎵 StemSplitter Pro - Personal Music Separation")
        self.root.geometry("900x700")
        self.root.configure(bg='#2b2b2b')
        
        # Application state
        self.processing_queue = []  # List of files to process
        self.current_stems = {}     # Currently loaded stems for preview
        self.output_directory = ""  # Where to save processed files
        self.processing = False     # Whether we're currently processing
        self.thread_queue = queue.Queue()  # For thread communication
        
        # Demucs model (will be loaded when first used)
        self.separator = None
        
        # Load settings
        self.settings = self.load_settings()
        
        # Setup GUI
        self.setup_gui()
        
        # Start checking for thread messages
        self.check_queue()
    
    def setup_gui(self):
        """Create and arrange all GUI elements"""
        
        # Main title
        title_frame = tk.Frame(self.root, bg='#2b2b2b')
        title_frame.pack(pady=10)
        
        title_label = tk.Label(
            title_frame, 
            text="🎵 StemSplitter Pro", 
            font=('Arial', 20, 'bold'),
            fg='#4a9eff',
            bg='#2b2b2b'
        )
        title_label.pack()
        
        subtitle_label = tk.Label(
            title_frame,
            text="Separate your music into stems and create custom practice tracks",
            font=('Arial', 10),
            fg='#cccccc',
            bg='#2b2b2b'
        )
        subtitle_label.pack()
        
        # File selection section
        self.setup_file_section()
        
        # Processing options section
        self.setup_options_section()
        
        # Queue/Progress section
        self.setup_queue_section()
        
        # Control buttons
        self.setup_control_section()
        
        # Status bar
        self.setup_status_bar()
    
    def setup_file_section(self):
        """Setup file selection and output directory section"""
        
        file_frame = tk.LabelFrame(
            self.root, 
            text="📁 File Selection", 
            font=('Arial', 12, 'bold'),
            fg='#4a9eff',
            bg='#2b2b2b'
        )
        file_frame.pack(fill='x', padx=10, pady=5)
        
        # Input files row
        input_row = tk.Frame(file_frame, bg='#2b2b2b')
        input_row.pack(fill='x', padx=10, pady=5)
        
        tk.Button(
            input_row,
            text="Add Songs",
            command=self.add_files,
            bg='#4a9eff',
            fg='white',
            font=('Arial', 10, 'bold'),
            padx=20
        ).pack(side='left', padx=(0, 10))
        
        tk.Button(
            input_row,
            text="Add Folder",
            command=self.add_folder,
            bg='#6a7eff',
            fg='white',
            font=('Arial', 10, 'bold'),
            padx=20
        ).pack(side='left', padx=(0, 10))
        
        self.file_count_label = tk.Label(
            input_row,
            text="0 songs in queue",
            fg='#cccccc',
            bg='#2b2b2b'
        )
        self.file_count_label.pack(side='left', padx=(20, 0))
        
        # Output directory row
        output_row = tk.Frame(file_frame, bg='#2b2b2b')
        output_row.pack(fill='x', padx=10, pady=5)
        
        tk.Label(
            output_row,
            text="Save to:",
            fg='#cccccc',
            bg='#2b2b2b'
        ).pack(side='left')
        
        self.output_label = tk.Label(
            output_row,
            text="Select output folder...",
            fg='#aaaaaa',
            bg='#2b2b2b',
            anchor='w'
        )
        self.output_label.pack(side='left', fill='x', expand=True, padx=(10, 0))
        
        tk.Button(
            output_row,
            text="Browse",
            command=self.select_output_folder,
            bg='#5a5a5a',
            fg='white'
        ).pack(side='right')
    
    def setup_options_section(self):
        """Setup processing options and presets"""
        
        options_frame = tk.LabelFrame(
            self.root,
            text="⚙️ Processing Options",
            font=('Arial', 12, 'bold'),
            fg='#4a9eff',
            bg='#2b2b2b'
        )
        options_frame.pack(fill='x', padx=10, pady=5)
        
        # Quality settings
        quality_row = tk.Frame(options_frame, bg='#2b2b2b')
        quality_row.pack(fill='x', padx=10, pady=5)
        
        tk.Label(
            quality_row,
            text="Quality:",
            fg='#cccccc',
            bg='#2b2b2b'
        ).pack(side='left')
        
        self.quality_var = tk.StringVar(value=self.settings.get('quality', 'high'))
        quality_combo = ttk.Combobox(
            quality_row,
            textvariable=self.quality_var,
            values=['fast', 'high'],
            state='readonly',
            width=10
        )
        quality_combo.pack(side='left', padx=(10, 0))
        ToolTip(quality_combo, msg="Fast: ~1min/song, High: ~3min/song but better quality")
        
        # GPU option
        self.use_gpu_var = tk.BooleanVar(value=self.settings.get('use_gpu', torch.cuda.is_available()))
        gpu_check = tk.Checkbutton(
            quality_row,
            text="Use GPU (if available)",
            variable=self.use_gpu_var,
            fg='#cccccc',
            bg='#2b2b2b',
            selectcolor='#2b2b2b'
        )
        gpu_check.pack(side='left', padx=(20, 0))
        
        # Output presets
        preset_row = tk.Frame(options_frame, bg='#2b2b2b')
        preset_row.pack(fill='x', padx=10, pady=5)
        
        tk.Label(
            preset_row,
            text="What to create:",
            fg='#cccccc',
            bg='#2b2b2b'
        ).pack(side='left')
        
        # Checkboxes for what to generate
        self.save_stems_var = tk.BooleanVar(value=self.settings.get('save_stems', True))
        self.save_no_vocals_var = tk.BooleanVar(value=self.settings.get('save_no_vocals', True))
        self.save_no_bass_var = tk.BooleanVar(value=self.settings.get('save_no_bass', False))
        self.save_no_drums_var = tk.BooleanVar(value=self.settings.get('save_no_drums', False))
        
        preset_options = tk.Frame(preset_row, bg='#2b2b2b')
        preset_options.pack(side='left', padx=(10, 0))
        
        tk.Checkbutton(preset_options, text="Individual stems", variable=self.save_stems_var,
                      fg='#cccccc', bg='#2b2b2b', selectcolor='#2b2b2b').pack(side='left')
        tk.Checkbutton(preset_options, text="No vocals (karaoke)", variable=self.save_no_vocals_var,
                      fg='#cccccc', bg='#2b2b2b', selectcolor='#2b2b2b').pack(side='left')
        tk.Checkbutton(preset_options, text="No bass", variable=self.save_no_bass_var,
                      fg='#cccccc', bg='#2b2b2b', selectcolor='#2b2b2b').pack(side='left')
        tk.Checkbutton(preset_options, text="No drums", variable=self.save_no_drums_var,
                      fg='#cccccc', bg='#2b2b2b', selectcolor='#2b2b2b').pack(side='left')
    
    def setup_queue_section(self):
        """Setup the processing queue display"""
        
        queue_frame = tk.LabelFrame(
            self.root,
            text="📋 Processing Queue",
            font=('Arial', 12, 'bold'),
            fg='#4a9eff',
            bg='#2b2b2b'
        )
        queue_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Queue listbox with scrollbar
        list_frame = tk.Frame(queue_frame, bg='#2b2b2b')
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        self.queue_listbox = tk.Listbox(
            list_frame,
            bg='#3b3b3b',
            fg='#cccccc',
            selectbackground='#4a9eff',
            font=('Courier', 9)
        )
        self.queue_listbox.pack(side='left', fill='both', expand=True)
        
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side='right', fill='y')
        self.queue_listbox.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.queue_listbox.yview)
        
        # Queue controls
        queue_controls = tk.Frame(queue_frame, bg='#2b2b2b')
        queue_controls.pack(fill='x', padx=10, pady=5)
        
        tk.Button(
            queue_controls,
            text="Remove Selected",
            command=self.remove_selected,
            bg='#ff6b6b',
            fg='white'
        ).pack(side='left', padx=(0, 10))
        
        tk.Button(
            queue_controls,
            text="Clear Queue",
            command=self.clear_queue,
            bg='#ff8787',
            fg='white'
        ).pack(side='left')
        
        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            queue_controls,
            variable=self.progress_var,
            maximum=100
        )
        self.progress_bar.pack(side='right', fill='x', expand=True, padx=(20, 0))
    
    def setup_control_section(self):
        """Setup main control buttons"""
        
        control_frame = tk.Frame(self.root, bg='#2b2b2b')
        control_frame.pack(fill='x', padx=10, pady=10)
        
        # Main process button
        self.process_button = tk.Button(
            control_frame,
            text="🚀 Start Processing",
            command=self.start_processing,
            bg='#51cf66',
            fg='white',
            font=('Arial', 12, 'bold'),
            padx=30,
            pady=10
        )
        self.process_button.pack(side='left', padx=(0, 20))
        
        # Stop button
        self.stop_button = tk.Button(
            control_frame,
            text="⏹️ Stop",
            command=self.stop_processing,
            bg='#ff6b6b',
            fg='white',
            font=('Arial', 12, 'bold'),
            padx=30,
            pady=10,
            state='disabled'
        )
        self.stop_button.pack(side='left')
        
        # Open output folder button
        self.open_folder_button = tk.Button(
            control_frame,
            text="📁 Open Results",
            command=self.open_output_folder,
            bg='#4a9eff',
            fg='white',
            font=('Arial', 12, 'bold'),
            padx=30,
            pady=10
        )
        self.open_folder_button.pack(side='right')
    
    def setup_status_bar(self):
        """Setup status bar at bottom"""
        
        self.status_var = tk.StringVar(value="Ready to separate some music! 🎵")
        self.status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            relief='sunken',
            anchor='w',
            bg='#1b1b1b',
            fg='#cccccc'
        )
        self.status_bar.pack(side='bottom', fill='x')
    
    def add_files(self):
        """Add individual audio files to the queue"""
        filetypes = [
            ('Audio files', '*.mp3 *.wav *.flac *.m4a *.aac *.ogg'),
            ('MP3 files', '*.mp3'),
            ('WAV files', '*.wav'),
            ('FLAC files', '*.flac'),
            ('All files', '*.*')
        ]
        
        files = filedialog.askopenfilenames(
            title="Select audio files to separate",
            filetypes=filetypes
        )
        
        for file in files:
            if file not in self.processing_queue:
                self.processing_queue.append(file)
        
        self.update_queue_display()
    
    def add_folder(self):
        """Add all audio files from a folder to the queue"""
        folder = filedialog.askdirectory(title="Select folder containing audio files")
        if not folder:
            return
        
        audio_extensions = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg'}
        added_count = 0
        
        for root, dirs, files in os.walk(folder):
            for file in files:
                if Path(file).suffix.lower() in audio_extensions:
                    full_path = os.path.join(root, file)
                    if full_path not in self.processing_queue:
                        self.processing_queue.append(full_path)
                        added_count += 1
        
        if added_count > 0:
            self.status_var.set(f"Added {added_count} songs from folder")
        else:
            self.status_var.set("No new audio files found in folder")
        
        self.update_queue_display()
    
    def select_output_folder(self):
        """Select where to save the processed files"""
        folder = filedialog.askdirectory(title="Select output folder for separated stems")
        if folder:
            self.output_directory = folder
            # Truncate long paths for display
            display_path = folder if len(folder) < 50 else "..." + folder[-47:]
            self.output_label.config(text=display_path, fg='#4a9eff')
    
    def update_queue_display(self):
        """Update the queue listbox display"""
        self.queue_listbox.delete(0, tk.END)
        
        for i, file_path in enumerate(self.processing_queue):
            filename = os.path.basename(file_path)
            # Show status icon and filename
            status = "⏳" if self.processing and i == 0 else "📝"
            display_text = f"{status} {filename}"
            self.queue_listbox.insert(tk.END, display_text)
        
        # Update file count
        count = len(self.processing_queue)
        self.file_count_label.config(text=f"{count} song{'s' if count != 1 else ''} in queue")
    
    def remove_selected(self):
        """Remove selected items from the queue"""
        selected = self.queue_listbox.curselection()
        if not selected:
            return
        
        # Remove in reverse order to maintain indices
        for index in reversed(selected):
            if index < len(self.processing_queue):
                del self.processing_queue[index]
        
        self.update_queue_display()
    
    def clear_queue(self):
        """Clear all items from the queue"""
        if self.processing:
            messagebox.showwarning("Processing Active", "Cannot clear queue while processing. Stop processing first.")
            return
        
        self.processing_queue.clear()
        self.update_queue_display()
    
    def start_processing(self):
        """Start processing the queue"""
        if not self.processing_queue:
            messagebox.showwarning("No Files", "Please add some audio files to the queue first.")
            return
        
        if not self.output_directory:
            messagebox.showwarning("No Output Folder", "Please select an output folder first.")
            return
        
        # Validate at least one output option is selected
        if not any([self.save_stems_var.get(), self.save_no_vocals_var.get(), 
                   self.save_no_bass_var.get(), self.save_no_drums_var.get()]):
            messagebox.showwarning("No Outputs", "Please select at least one output option.")
            return
        
        # Start processing in background thread
        self.processing = True
        self.process_button.config(state='disabled')
        self.stop_button.config(state='normal')
        
        processing_thread = threading.Thread(target=self.process_files, daemon=True)
        processing_thread.start()
    
    def stop_processing(self):
        """Stop the current processing"""
        self.processing = False
        self.status_var.set("Stopping processing...")
    
    def process_files(self):
        """Process all files in the queue (runs in background thread)"""
        try:
            # Initialize Demucs if not already done
            if self.separator is None:
                self.thread_queue.put(("status", "Loading AI model... (this may take a minute)"))
                model_name = "htdemucs_ft" if self.quality_var.get() == "high" else "htdemucs"
                device = "cuda" if self.use_gpu_var.get() and torch.cuda.is_available() else "cpu"
                self.separator = demucs.api.Separator(model=model_name, device=device)
                self.thread_queue.put(("status", f"Model loaded on {device.upper()}! Starting separation..."))
            
            total_files = len(self.processing_queue)
            
            for i, file_path in enumerate(self.processing_queue.copy()):
                if not self.processing:  # Check if user stopped
                    break
                
                try:
                    filename = os.path.basename(file_path)
                    self.thread_queue.put(("status", f"Processing: {filename}"))
                    self.thread_queue.put(("progress", (i / total_files) * 100))
                    self.thread_queue.put(("queue_update", None))
                    
                    # Separate the audio
                    waveform, rate = librosa.load(file_path, sr=44100, mono=False)
                    if waveform.ndim == 1:
                        waveform = np.expand_dims(waveform, 0)  # Make stereo if mono
                    
                    # Convert to torch tensor for Demucs
                    waveform_tensor = torch.from_numpy(waveform).float()
                    
                    # Separate stems
                    separated = self.separator.separate_tensor(waveform_tensor)
                    
                    # Create output folder for this song
                    song_name = Path(file_path).stem
                    song_folder = os.path.join(self.output_directory, song_name)
                    os.makedirs(song_folder, exist_ok=True)
                    
                    # Save individual stems if requested
                    if self.save_stems_var.get():
                        for stem_name, stem_audio in separated.items():
                            stem_path = os.path.join(song_folder, f"{song_name}_{stem_name}.wav")
                            sf.write(stem_path, stem_audio.numpy().T, rate)
                    
                    # Create mixed versions with tracks muted
                    if self.save_no_vocals_var.get():
                        # Mix everything except vocals
                        no_vocals = sum(audio for name, audio in separated.items() if name != "vocals")
                        no_vocals_path = os.path.join(song_folder, f"{song_name}_no_vocals.wav")
                        sf.write(no_vocals_path, no_vocals.numpy().T, rate)
                    
                    if self.save_no_bass_var.get():
                        # Mix everything except bass
                        no_bass = sum(audio for name, audio in separated.items() if name != "bass")
                        no_bass_path = os.path.join(song_folder, f"{song_name}_no_bass.wav")
                        sf.write(no_bass_path, no_bass.numpy().T, rate)
                    
                    if self.save_no_drums_var.get():
                        # Mix everything except drums
                        no_drums = sum(audio for name, audio in separated.items() if name != "drums")
                        no_drums_path = os.path.join(song_folder, f"{song_name}_no_drums.wav")
                        sf.write(no_drums_path, no_drums.numpy().T, rate)
                    
                    # Remove processed file from queue
                    if file_path in self.processing_queue:
                        self.processing_queue.remove(file_path)
                    
                except Exception as e:
                    self.thread_queue.put(("error", f"Error processing {filename}: {str(e)}"))
                    continue
            
            # Processing complete
            if self.processing:  # Only if not stopped by user
                self.thread_queue.put(("status", "All files processed successfully! 🎉"))
                self.thread_queue.put(("complete", None))
            else:
                self.thread_queue.put(("status", "Processing stopped by user"))
            
        except Exception as e:
            self.thread_queue.put(("error", f"Processing error: {str(e)}"))
        
        finally:
            self.thread_queue.put(("progress", 0))
            self.processing = False
    
    def check_queue(self):
        """Check for messages from background thread"""
        try:
            while True:
                message_type, data = self.thread_queue.get_nowait()
                
                if message_type == "status":
                    self.status_var.set(data)
                elif message_type == "progress":
                    self.progress_var.set(data)
                elif message_type == "queue_update":
                    self.update_queue_display()
                elif message_type == "error":
                    messagebox.showerror("Processing Error", data)
                elif message_type == "complete":
                    self.process_button.config(state='normal')
                    self.stop_button.config(state='disabled')
                    self.progress_var.set(100)
        
        except queue.Empty:
            pass
        
        # Check again in 100ms
        self.root.after(100, self.check_queue)
    
    def open_output_folder(self):
        """Open the output folder in file explorer"""
        if not self.output_directory:
            messagebox.showinfo("No Output Folder", "Please select an output folder first.")
            return
        
        if not os.path.exists(self.output_directory):
            messagebox.showwarning("Folder Not Found", "Output folder doesn't exist yet.")
            return
        
        # Open folder in OS file manager
        if sys.platform == "win32":
            os.startfile(self.output_directory)
        elif sys.platform == "darwin":  # macOS
            subprocess.run(["open", self.output_directory])
        else:  # Linux
            subprocess.run(["xdg-open", self.output_directory])
    
    def load_settings(self):
        """Load settings from config file"""
        settings_file = os.path.join(os.path.expanduser("~"), ".stemsplitter_settings.json")
        default_settings = {
            'quality': 'high',
            'use_gpu': torch.cuda.is_available(),
            'save_stems': True,
            'save_no_vocals': True,
            'save_no_bass': False,
            'save_no_drums': False
        }
        
        try:
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    return {**default_settings, **json.load(f)}
        except:
            pass
        
        return default_settings
    
    def save_settings(self):
        """Save current settings to config file"""
        settings_file = os.path.join(os.path.expanduser("~"), ".stemsplitter_settings.json")
        settings = {
            'quality': self.quality_var.get(),
            'use_gpu': self.use_gpu_var.get(),
            'save_stems': self.save_stems_var.get(),
            'save_no_vocals': self.save_no_vocals_var.get(),
            'save_no_bass': self.save_no_bass_var.get(),
            'save_no_drums': self.save_no_drums_var.get()
        }
        
        try:
            with open(settings_file, 'w') as f:
                json.dump(settings, f)
        except:
            pass
    
    def on_closing(self):
        """Handle application closing"""
        if self.processing:
            if messagebox.askokcancel("Processing Active", 
                                    "Processing is still running. Stop and quit?"):
                self.processing = False
                self.save_settings()
                self.root.destroy()
        else:
            self.save_settings()
            self.root.destroy()


def main():
    """Main entry point for the application"""
    
    # Check for required packages
    try:
        import torch
        import demucs.api
        import librosa
        import soundfile as sf
    except ImportError as e:
        print("=" * 60)
        print("🚨 MISSING REQUIRED PACKAGES")
        print("=" * 60)
        print("Please install the required packages by running:")
        print()
        print("pip install demucs torch torchaudio librosa soundfile tkinter-tooltip")
        print()
        print("For GPU acceleration (recommended), also run:")
        print("pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118")
        print()
        print(f"Error details: {e}")
        print("=" * 60)
        return
    
    # Create and run the application
    root = tk.Tk()
    app = StemSplitterApp(root)
    
    # Handle window closing
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    # Center window on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (root.winfo_width() // 2)
    y = (root.winfo_screenheight() // 2) - (root.winfo_height() // 2)
    root.geometry(f"+{x}+{y}")
    
    print("🎵 StemSplitter Pro started!")
    print("Loading AI model on first use - this may take a minute...")
    
    # Start the GUI
    root.mainloop()


if __name__ == "__main__":
    main()