


class Camera:

    def __init__(self):
        pass

    def capture(self) -> str:
        if not self.camera.isOpened():
            return None
        ret, frame = self.camera.read()
        if not ret:
            return None
        _, buffer = cv2.imencode('.jpg', frame)
        return base64.b64encode(buffer).decode('utf-8')


class Speaker:

    def __init__(self):
        pass

    def play_text(self, text):
        # TODO: Play text and don't block the thread (i.e. play the audio in the background)
        pass

    def is_playing(self) -> bool:
        # TODO: Check if the is speaker playing
        pass


class Microphone:
    
    def __init__(self):
        # TODO: Continuously detect for audio in the background (don't block the thread, spin up a thread or process)
        pass

    def has_text(self, text) -> str:
        # TODO: Check if there is text detected by the microphone
        pass

    def read_text(self) -> str:
        # TODO: return the text
        pass