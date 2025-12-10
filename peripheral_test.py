import base64
import time
import threading
import queue

class Camera:
    def __init__(self):
        # Ensure you have a file named 'test_image.jpg' in the same directory
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

    def __init__(self):
        pass

    def play_text(self, text):
        #print(f"Speaker: {text}")
        pass

    def is_playing(self) -> bool:
        return False


class Microphone:
    def __init__(self):
        self.input_queue = queue.Queue()
        # Start a background thread to read console input without blocking execution
        self.listener_thread = threading.Thread(target=self._input_listener, daemon=True)
        self.listener_thread.start()

    def _input_listener(self):
        print("\n[Mock Mic] Background listener active. Type a command and press Enter anytime to simulate voice input.")
        while True:
            try:
                # This input() blocks this thread, but not the main program
                text = input()
                if text.strip():
                    self.input_queue.put(text.strip())
            except EOFError:
                break

    def has_text(self) -> bool:
        return not self.input_queue.empty()

    def read_text(self) -> str:
        try:
            return self.input_queue.get_nowait()
        except queue.Empty:
            return ""