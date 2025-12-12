import threading
import queue
import pyttsx3
#from picamera2 import Picamera2
import base64, time
import speech_recognition as sr
import os
import sys
from contextlib import contextmanager

# --- CAMERA CLASS ---
class Camera:
    def __init__(self):
        # Ensure you have a file named 'images.jfif' in the same directory
        self.image_path = "images.jfif"

    def capture(self) -> str:
        try:
            with open(self.image_path, "rb") as image_file:
                # Read binary file and encode to base64 string
                print(f"[Mock Camera] Captured {self.image_path}")
                return base64.b64encode(image_file.read()).decode('utf-8')
        except FileNotFoundError:
            print(f"[Mock Camera] Error: '{self.image_path}' not found. Please place an image file in the directory.")
            return None

# --- SPEAKER CLASS ---
class Speaker:
    def __init__(self, microphone=None):
        self.microphone = microphone
        self._queue = queue.Queue()
        self._is_speaking_event = threading.Event()  # Thread-safe flag
        
        # Start the persistent worker thread
        self._thread = threading.Thread(target=self._play_worker, daemon=True)
        self._thread.start()

    def _play_worker(self):
        """
        Initializes the engine on this thread and waits for text to speak.
        """
        # Initialize engine once on the worker thread
        engine = pyttsx3.init()
        engine.setProperty('rate', 150)

        while True:
            # 1. Block until an item is available
            first_text = self._queue.get()
            
            # 2. Mark as busy and pause microphone
            self._is_speaking_event.set()
            if self.microphone:
                self.microphone.pause()

            try:
                # Process the first item
                self._speak_one(engine, first_text)

                # 3. Drain the rest of the queue to keep mic paused between sentences
                while not self._queue.empty():
                    try:
                        next_text = self._queue.get_nowait()
                        self._speak_one(engine, next_text)
                    except queue.Empty:
                        break
            finally:
                # 4. Resume microphone and clear busy flag
                if self.microphone:
                    self.microphone.resume()
                self._is_speaking_event.clear()

    def _speak_one(self, engine, text):
        try:
            # print(f"🗣️ Speaking: {text}") 
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"Error in speech generation: {e}")
        finally:
            self._queue.task_done()

    def play_text(self, text):
        if isinstance(text, list):
            for t in text:
                self._queue.put(t)
        else:
            self._queue.put(text)

    def is_playing(self):
        return self._is_speaking_event.is_set() or not self._queue.empty()

# --- MICROPHONE CLASS ---

# Context manager to suppress stderr (ALSA/JACK errors)
@contextmanager
def ignore_stderr():
    try:
        # Open a null device
        devnull = os.open(os.devnull, os.O_WRONLY)
        # Save the current stderr so we can restore it later
        old_stderr = os.dup(2)
        sys.stderr.flush()
        # Redirect stderr to null
        os.dup2(devnull, 2)
        os.close(devnull)
        yield
    finally:
        # Restore stderr
        os.dup2(old_stderr, 2)
        os.close(old_stderr)

class Microphone:
    def __init__(self):
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event() 
        
        self._thread = threading.Thread(
            target=self._listen_worker,
            daemon=True,
        )
        self._thread.start()

    def pause(self):
        """Stop microphone from listening (speaker is talking)."""
        self._pause_event.set()

    def resume(self):
        """Resume microphone listening."""
        self._pause_event.clear()

    def _listen_worker(self):
        recognizer = sr.Recognizer()
        
        # 1. Initialize Microphone (Silenced)
        # Wrapping this suppresses the "ALSA lib..." logs during init
        try:
            with ignore_stderr():
                mic = sr.Microphone(device_index=0)
        except OSError:
            print("⚠️ [Mic] Error: Device index 0 not found. Check audio settings.")
            return

        # 2. Ambient Noise Adjustment (Silenced)
        # This opens the stream, which also triggers ALSA logs
        print("[Mic] Adjusting for ambient noise...")
        with ignore_stderr():
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
        
        print("[Mic] Listening...")

        while not self._stop_event.is_set():

            # If paused → do nothing but sleep briefly
            if self._pause_event.is_set():
                time.sleep(0.1)
                continue

            try:
                # 3. Listening (Silenced)
                # 'listen' keeps the stream open
                with ignore_stderr():
                    with mic as source:
                        audio = recognizer.listen(source, timeout=None, phrase_time_limit=5)

                # 4. Recognition (No need to silence, this is network/CPU only)
                try:
                    # Using zh-TW for Taiwan context
                    text = recognizer.recognize_google(audio, language="zh-TW")
                    if text.strip():
                        # print(f"[Mic] Recognized: {text}")
                        self._queue.put(text.strip())
                except sr.UnknownValueError:
                    pass
                except sr.RequestError as e:
                    print(f"[Mic] Network Error: {e}")

            except Exception:
                continue

    def has_text(self):
        return not self._queue.empty()

    def read_text(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return ""

if __name__ == "__main__":
    mic = Microphone()

    try:
        print("Listening for speech... Press Ctrl+C to stop.")
        while True:
            if mic.has_text():
                print("Recognized:", mic.read_text())
            time.sleep(1)

    except KeyboardInterrupt:
        print("Test interrupted. Stopped listening.")
        mic._stop_event.set()