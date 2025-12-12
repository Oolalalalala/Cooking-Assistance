import threading
import queue
import pyttsx3
#from picamera2 import Picamera2
import base64, time
import speech_recognition as sr
import os
import sys
from contextlib import contextmanager
import re, tempfile, subprocess

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

class Speaker:
    def __init__(
        self,
        microphone=None,
        # Adjust these paths to where you actually installed Piper
        piper_bin=os.path.expanduser("~/.local/bin/piper"),
        model_path=os.path.expanduser("~/piper/models/zh_CN-huayan-medium.onnx"),
        sentence_silence=0.3,
        volume=0.7,
        use_queue=True,
    ):
        self.microphone = microphone
        self._lock = threading.Lock()
        self._is_playing = False

        self.piper_bin = piper_bin
        self.model_path = model_path
        self.sentence_silence = sentence_silence
        self.volume = volume

        # Sanity checks for Piper paths
        if not os.path.exists(self.piper_bin):
            print(f"⚠️ Warning: Piper binary not found at {self.piper_bin}")
        if not os.path.exists(self.model_path):
            print(f"⚠️ Warning: Model not found at {self.model_path}")

        self._use_queue = use_queue
        self._queue = queue.Queue()
        
        # Start the persistent worker thread
        if use_queue:
            self._thread = threading.Thread(target=self._play_worker, daemon=True)
            self._thread.start()

    def _set_playing(self, v: bool):
        with self._lock:
            self._is_playing = v

    def is_playing(self):
        """Returns True if audio is playing or if queue is not empty."""
        with self._lock:
            return self._is_playing or not self._queue.empty()

    def _normalize_for_piper(self, text: str) -> str:
        """Optimizes punctuation for better TTS flow."""
        text = re.sub(r"[。！？]", lambda m: m.group(0) + "\n", text)  # Newline after sentence end
        text = re.sub(r"[，、]", " ", text)  # Pause for commas
        text = re.sub(r"\n{2,}", "\n", text)  # Remove excessive newlines
        return text.strip()

    def _speak_one(self, text: str):
        """Generates WAV with Piper and plays it via aplay."""
        text = self._normalize_for_piper(text)

        # Create a temp file for the audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name

        try:
            # Pause microphone to prevent hearing itself
            if self.microphone:
                self.microphone.pause()

            self._set_playing(True)

            # 1. Generate Audio (Piper)
            cmd = [
                self.piper_bin,
                "-m", self.model_path,
                "-f", wav_path,
                "--sentence-silence", str(self.sentence_silence),
                "--volume", str(self.volume),
                "--noise-scale", "0.6",
                "--noise-w-scale", "0.6",
            ]

            # print(f"🗣️ Piper generating: {text[:20]}...")
            r = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            if r.returncode != 0:
                print("❌ Piper Failed:", r.stderr.decode("utf-8", errors="ignore"))
                return

            if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
                print("❌ Generated WAV is empty.")
                return

            # 2. Play Audio (aplay)
            r2 = subprocess.run(
                ["aplay", "-q", wav_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if r2.returncode != 0:
                print("❌ aplay Failed:", r2.stderr.decode("utf-8", errors="ignore"))

        except Exception as e:
            print(f"Error in speech loop: {e}")
        finally:
            self._set_playing(False)
            if self.microphone:
                self.microphone.resume()
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def _play_worker(self):
        """Worker loop that pulls text from queue and speaks it."""
        while True:
            text = self._queue.get()
            if text is None:
                self._queue.task_done()
                return
            try:
                self._speak_one(text)
            finally:
                self._queue.task_done()

    def play_text(self, text):
        """Interface used by the main app to queue text."""
        if isinstance(text, list):
            for t in text:
                if self._use_queue:
                    self._queue.put(t)
                else:
                    self._speak_one(t) # Blocking fallback
        else:
            if self._use_queue:
                self._queue.put(text)
            else:
                threading.Thread(target=self._speak_one, args=(text,), daemon=True).start()

    def close(self):
        if self._use_queue:
            self._queue.put(None)

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
                        audio = recognizer.listen(source, timeout=None, phrase_time_limit=10)

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
        messages = []
        try:
            # Loop strictly to drain the queue
            while True:
                # get_nowait raises queue.Empty immediately if nothing is there
                msg = self._queue.get_nowait()
                messages.append(msg)
                self._queue.task_done()
        except queue.Empty:
            pass # Queue is empty, loop finished
            
        # Join all separate phrases into one string (e.g. "Yes" + "I am ready")
        return " ".join(messages)

if __name__ == "__main__":

    try:
        speaker = Speaker(microphone=None)

        print("🗣️ Speaking...")
        speaker.play_text("測試成功，語音系統正常。")

        # Wait for audio to finish
        while speaker.is_playing():
            time.sleep(0.1)

        print("✅ Done.")

    except KeyboardInterrupt:
        print("Test interrupted. Stopped listening.")
        mic._stop_event.set()


