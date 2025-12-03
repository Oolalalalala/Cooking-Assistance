import os
import cv2
import base64
import json
import time
import speech_recognition as sr
import pyttsx3
import requests
import re
from dotenv import load_dotenv
from datetime import datetime

# Load API Key
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")

class State:
    """
    A data structure representing a single state in the FSM.
    It does not contain logic, only configuration.
    """
    def __init__(self, name, description, valid_next_states, requires_image=False):
        self.name = name
        self.description = description
        self.valid_next_states = valid_next_states  # List of strings (Keys in the state dict)
        self.requires_image = requires_image        # Boolean: Does this state need camera input?

class CookingAssistant:
    def __init__(self):
        self.api_key = API_KEY
        self.history = []  # Stores conversation history for GPT context
        
        # --- Hardware Setup ---
        self.recognizer = sr.Recognizer()
        self.mic = sr.Microphone()
        self.engine = pyttsx3.init()
        self.engine.setProperty('rate', 150)
        self.camera = cv2.VideoCapture(0)
        
        # --- State Definitions ---
        # We define the behavior of the system purely through data here.
        self.states = {
            "START": State(
                name="START",
                description="Initial state. Introduce yourself and ask the user to show the ingredients.",
                valid_next_states=["INGREDIENT_SCAN"],
                requires_image=False
            ),
            "INGREDIENT_SCAN": State(
                name="INGREDIENT_SCAN",
                description="Analyze the image to identify ingredients. Propose a dish based on them. If the ingredients are unclear, tell the user to rearrange the ingredients and remain in the INGREDIENT_SCAN state.",
                valid_next_states=["RECIPE_CONFIRMATION", "INGREDIENT_SCAN"], # Scan again if unclear
                requires_image=True
            ),
            "RECIPE_CONFIRMATION": State(
                name="RECIPE_CONFIRMATION",
                description="Negotiate the recipe with the user. If the user says yes, start cooking and move to INSTRUCTION_OVERVIEW state. If no, propose another dish or make modification on the recipe depending on the user's response and remain in the RECIPE_CONFIRMATION state.",
                valid_next_states=["RECIPE_CONFIRMATION", "INSTRUCTION_OVERVIEW"],
                requires_image=False
            ),
            "INSTRUCTION_OVERVIEW": State(
                name="INSTRUCTION_OVERVIEW",
                description="Give an overview of the instructions.",
                valid_next_states=["COOKING_INSTRUCTION"],
                requires_image=False
            ),
            "COOKING_INSTRUCTION": State(
                name="COOKING_INSTRUCTION",
                description="Provide the next cooking step. Move to MONITORING as the next state. If done, move to FINISHED.",
                valid_next_states=["MONITORING", "FINISHED"],
                requires_image=False
            ),
            "MONITORING": State(
                name="MONITORING",
                description="""
                Check the cooking progress visually (doneness, chopping size, burning).
                1. If everything is normal and the step is still in progress, set speech_output to 'MONITOR_NORMAL' and stay in MONITORING.
                2. If all instructions have been finished, move to FINISHED.
                3. If the current instruction has been completed, move to COOKING_INSTRUCTION.
                4. If the user has clearly made a mistake (e.g., chopping instead of peeling), go to ERROR_CORRECTION.
                5. If the current step implies a specific time duration (e.g., 'boil for 10 mins') and you see the user has just started this action successfully, set speech_output to 'START_TIMER'.
                """,
                valid_next_states=["COOKING_INSTRUCTION", "ERROR_CORRECTION", "MONITORING", "FINISHED"],
                requires_image=True
            ),
            "ERROR_CORRECTION": State(
                name="ERROR_CORRECTION",
                description="Explain how to fix the detected error. Once fixed, return to instructions.",
                valid_next_states=["COOKING_INSTRUCTION", "MONITORING"],
                requires_image=True
            ),
            "FINISHED": State(
                name="FINISHED",
                description="Congratulate the user and end the session.",
                valid_next_states=[],
                requires_image=False
            )
        }
        
        self.current_state_name = "START"

    def speak(self, text):
        if text:
            print(f"🤖 AI: {text}")
            self.engine.say(text)
            self.engine.runAndWait()

    def listen(self):
        with self.mic as source:
            print("🎤 Listening...")
            self.recognizer.adjust_for_ambient_noise(source)
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=5)
                text = self.recognizer.recognize_google(audio)
                print(f"👤 User: {text}")
                return text
            except (sr.WaitTimeoutError, sr.UnknownValueError):
                return ""

    def capture_image(self):
        if not self.camera.isOpened():
            return None
        ret, frame = self.camera.read()
        if not ret:
            return None
        _, buffer = cv2.imencode('.jpg', frame)
        return base64.b64encode(buffer).decode('utf-8')

    def update_history(self, role, content):
        """
        Manages history updates.
        1. Adds new messages.
        2. Merges repeating 'MONITOR_NORMAL' assistant states with a counter (e.g., 'MONITOR_NORMAL * 3').
        """
        # If adding an assistant message, check for "MONITOR_NORMAL" merging logic
        if role == "assistant":
            try:
                # content passed here is the JSON string
                content_json = json.loads(content)
                current_speech = content_json.get("speech_output", "")

                # We only attempt merge if current response is exactly "MONITOR_NORMAL"
                if current_speech == "MONITOR_NORMAL" and len(self.history) >= 2:
                    # Check if previous assistant message was also NORMAL (or NORMAL * N)
                    # History structure: [..., prev_user_msg (idx-3), prev_assistant_msg (idx-2), current_user_msg (idx-1)]
                    prev_assistant_msg = self.history[-2] 
                    
                    if prev_assistant_msg["role"] == "assistant":
                        try:
                            prev_json = json.loads(prev_assistant_msg["content"])
                            prev_speech = prev_json.get("speech_output", "")
                            
                            # Check if previous speech matches "MONITOR_NORMAL" or "MONITOR_NORMAL * N"
                            match = re.match(r"^MONITOR_NORMAL(?: \* (\d+))?$", prev_speech)
                            
                            if match:
                                # Determine current count
                                count_str = match.group(1)
                                count = int(count_str) if count_str else 1
                                
                                # Increment count
                                new_count = count + 1
                                
                                # Update the CURRENT content to reflect the new total count
                                content_json["speech_output"] = f"MONITOR_NORMAL * {new_count}"
                                content = json.dumps(content_json)
                                
                                print(f"[System] Merging MONITOR_NORMAL state. Count: {new_count}")
                                
                                # Remove the previous cycle (User input + Old Assistant response)
                                # We remove index -3 (Old User) and -2 (Old Assistant). 
                                # Index -1 is the Current User message, which we keep.
                                if len(self.history) >= 3:
                                    del self.history[-3] # Remove old user msg
                                    del self.history[-2] # Remove old assistant msg
                                    
                        except json.JSONDecodeError:
                            pass
            except json.JSONDecodeError:
                pass

        self.history.append({"role": role, "content": content})


    def call_gpt_api(self, user_voice, image_base64=None):
        """
        Calls GPT with the Current State Configuration + History.
        """
        current_state_obj = self.states[self.current_state_name]

        # 1. System Prompt
        system_prompt = """
        You are a Cooking Assistant Robot controlled by a Finite State Machine.
        You will receive the CURRENT STATE definition. 
        Your job is to:
        1. Read the user input/image.
        2. Generate a helpful response (speech).
        3. Decide the NEXT STATE from the list of valid transitions.
        
        OUTPUT JSON FORMAT:
        {
            "speech_output": "Text to speak to the user",
            "next_state": "Exact string name of the next state"
        }
        """

        # 2. Dynamic Input for this specific turn
        turn_context = {
            "current_state": current_state_obj.name,
            "state_goal": current_state_obj.description,
            "valid_next_states": current_state_obj.valid_next_states,
            "user_voice_input": user_voice,
            "image_provided": image_base64 is not None
        }

        # 3. Construct Message Payload
        user_message = [
            {"type": "text", "text": json.dumps(turn_context)}
        ]
        
        if image_base64:
            user_message.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            })

        # 4. Add to History (using the helper method, though User msgs don't trigger merge)
        # Note: We append User message directly or use a simple append, 
        # but using update_history standardizes it.
        self.history.append({"role": "user", "content": user_message})

        # 5. API Request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": system_prompt}] + self.history,
            "response_format": {"type": "json_object"},
            "max_tokens": 300
        }

        try:
            response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            data = response.json()
            
            if "error" in data:
                print(f"API Error: {data['error']}")
                return None
                
            assistant_content = data['choices'][0]['message']['content']
            
            # Add reply to history (Trigger logic to merge repeating NORMALs)
            self.update_history("assistant", assistant_content)
            
            return json.loads(assistant_content)

        except Exception as e:
            print(f"Network/Parsing Error: {e}")
            return None

    def run(self):
        self.speak("System starting.")
        
        while True:
            # 1. Get Current State Object
            state_obj = self.states[self.current_state_name]
            print(f"\n--- State: {state_obj.name} ---")

            # 2. Gather Inputs
            image_data = None
            if state_obj.requires_image:
                print("📸 State requires vision. Capturing...")
                image_data = self.capture_image()
            
            # Listen (Wait for input if not in initial start)
            user_voice = self.listen()

            # 3. Consult GPT
            if not user_voice and not image_data and self.current_state_name != "START":
                continue

            response = self.call_gpt_api(user_voice, image_data)
            
            if not response:
                continue

            # 4. Act on Response
            speech = response.get("speech_output")
            next_state = response.get("next_state")
            
            if speech == "MONITOR_NORMAL":
                print("✅ Status Normal... Continuing monitoring.")
                time.sleep(2)
            
            elif speech == "START_TIMER":
                print("⏳ Timer request detected.")
                
                # Logic to record start time
                current_time_str = datetime.now().strftime("%H:%M:%S")
                print(f"Timer started at: {current_time_str}")
                
                # Inject this system event into history so GPT knows about it next turn
                # We can append it as a User role message (System Notification)
                self.history.append({
                    "role": "user", 
                    "content": f"[System Notification: Timer successfully started at {current_time_str}]"
                })
                
                self.speak(f"Okay, I've started the timer at {current_time_str}.")
                
            else:
                self.speak(speech)
            
            # 5. Transition
            if next_state in self.states:
                self.current_state_name = next_state
            elif next_state == "FINISHED":
                print("Cooking Session Complete.")
                break
            else:
                print(f"Warning: GPT suggested invalid state '{next_state}'. Staying in {self.current_state_name}.")

        self.cleanup()

    def cleanup(self):
        self.camera.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    if not API_KEY:
        print("Please set OPENAI_API_KEY environment variable.")
    else:
        bot = CookingAssistant()
        bot.run()