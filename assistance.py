import os
import json
import time
import requests
import re
from dotenv import load_dotenv
from datetime import datetime
# Import the interface provided by your teammates
#from peripheral_test import Camera, Speaker, Microphone
from peripheral import MockCamera, Speaker, Microphone

# Load API Key
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")

class State:
    def __init__(self, name, description, valid_next_states, requires_image=False):
        self.name = name
        self.description = description
        self.valid_next_states = valid_next_states
        self.requires_image = requires_image

class CookingAssistant:
    def __init__(self, debug=True):
        self.api_key = API_KEY
        self.history = []
        self.debug = debug
        self.active_timers = [] # List to store dicts: {'name': str, 'end_time': float}
        
        # --- Hardware Initialization ---
        self.speaker = Speaker()
        self.camera = MockCamera()
        self.mic = Microphone()
        
        # --- State Definitions ---
        self.states = {
            "START": State(
                name="START",
                description="Initial state. Introduce yourself and ask the human to show the ingredients.",
                valid_next_states=["INGREDIENT_SCAN"],
                requires_image=False
            ),
            "INGREDIENT_SCAN": State(
                name="INGREDIENT_SCAN",
                description="Analyze the image to identify ingredients. Propose a dish based on them.",
                valid_next_states=["RECIPE_CONFIRMATION", "INGREDIENT_SCAN"],
                requires_image=True
            ),
            "RECIPE_CONFIRMATION": State(
                name="RECIPE_CONFIRMATION",
                description="Negotiate with the human. If the human agrees to the dish, move to INSTRUCTION_OVERVIEW. If no, propose another dish.",
                valid_next_states=["RECIPE_CONFIRMATION", "INSTRUCTION_OVERVIEW"],
                requires_image=True
            ),
            "INSTRUCTION_OVERVIEW": State(
                name="INSTRUCTION_OVERVIEW",
                description="Give a high-level overview of the instructions. If the user agrees to start, move to ACTIVE_COOKING",
                valid_next_states=["INSTRUCTION_OVERVIEW", "ACTIVE_COOKING"],
                requires_image=False
            ),
            "ACTIVE_COOKING": State(
                name="ACTIVE_COOKING",
                description="""
                The main execution loop.
                1. Instruct the user on the current step.
                2. Visually monitor progress.
                3. Answer ANY user questions.
                4. Handle timers.
                5. When the entire recipe is done, transition to FINISHED.
                """,
                valid_next_states=["ACTIVE_COOKING", "FINISHED"],
                requires_image=True
            ),
            "FINISHED": State(
                name="FINISHED",
                description="Congratulate the human and end the session.",
                valid_next_states=[],
                requires_image=False
            )
        }
        
        self.current_state_name = "START"

    def speak(self, text):
        print(f"🤖 AI: {text}")
        while self.speaker.is_playing():
            time.sleep(0.1)
        self.speaker.play_text(text)

    def listen(self, timeout=5):
        """
        Polls Microphone.has_text() for a duration.
        Returns text string or empty string.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.mic.has_text():
                text = self.mic.read_text()
                if text:
                    print(f"👤 User: {text}")
                    return text
            time.sleep(0.1)
        return ""

    def capture_image(self):
        return self.camera.capture()

    def _save_history(self):
        if self.debug:
            try:
                with open("history_log.json", "w", encoding='utf-8') as f:
                    json.dump(self.history, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def _prune_active_cooking_images(self):
        """
        Amnesia Strategy: Keeps only the latest 3 images from the ACTIVE_COOKING state.
        Removes the 'image_url' field from older messages to save tokens.
        """
        active_cooking_image_indices = []

        # 1. Identify all User messages that have images AND are in ACTIVE_COOKING
        for i, msg in enumerate(self.history):
            if msg["role"] == "user" and isinstance(msg["content"], list):
                # Check if this message has an image
                has_image = any(item.get("type") == "image_url" for item in msg["content"])
                
                if has_image:
                    # Check if the state context was ACTIVE_COOKING
                    try:
                        # Extract the text part which contains the JSON context
                        text_part = next(item for item in msg["content"] if item.get("type") == "text")
                        context = json.loads(text_part["text"])
                        
                        if context.get("current_state") == "ACTIVE_COOKING":
                            active_cooking_image_indices.append(i)
                    except (StopIteration, json.JSONDecodeError, KeyError):
                        continue

        # 2. Prune if count > 3 (Keep the last 3)
        if len(active_cooking_image_indices) > 3:
            # Indices to prune: All except the last 3
            indices_to_prune = active_cooking_image_indices[:-3]
            
            for idx in indices_to_prune:
                # Rebuild content list EXCLUDING the image_url item
                original_content = self.history[idx]["content"]
                new_content = [item for item in original_content if item.get("type") != "image_url"]
                
                # Add a placeholder so the AI knows an image used to be there
                new_content.append({
                    "type": "text", 
                    "text": "[System Note: Older image data stripped to save memory]"
                })
                
                self.history[idx]["content"] = new_content
                if self.debug:
                    print(f"[System] Amnesia: Pruned old image at history index {idx}")

    def update_history(self, role, content):
        """
        Smart History Management.
        """
        if role == "assistant":
            try:
                content_json = json.loads(content)
                status = content_json.get("status", "")
                speech = content_json.get("speech_output", "")

                if status == "MONITORING_NO_CHANGE" and not speech and len(self.history) >= 2:
                    prev_assistant_msg = self.history[-2] 
                    if prev_assistant_msg["role"] == "assistant":
                        try:
                            prev_json = json.loads(prev_assistant_msg["content"])
                            prev_status = prev_json.get("status", "")
                            
                            if prev_status == "MONITORING_NO_CHANGE" or "MONITORING_NO_CHANGE *" in prev_json.get("debug_note", ""):
                                count = prev_json.get("monitor_count", 1) + 1
                                content_json["monitor_count"] = count
                                content_json["debug_note"] = f"MONITORING_NO_CHANGE * {count}"
                                
                                content = json.dumps(content_json)
                                print(f"[System] Merging Monitor Log. Count: {count}")
                                
                                if len(self.history) >= 3:
                                    del self.history[-3]
                                    del self.history[-2]
                        except json.JSONDecodeError:
                            pass
            except json.JSONDecodeError:
                pass

        self.history.append({"role": role, "content": content})
        
        # --- NEW: Trigger Amnesia Strategy ---
        if role == "user":
            self._prune_active_cooking_images()
            
        self._save_history()

    def call_gpt_api(self, user_voice, image_base64=None):
        current_state_obj = self.states[self.current_state_name]

        # --- SYSTEM PROMPT ---
        system_prompt = """
        You are a Smart Cooking Assistant.

        ### LANGUAGE PROTOCOL ###
        - CRITICAL: All content in "speech_output" MUST be in Traditional Chinese (Taiwan/繁體中文).
        - Internal JSON values (status, next_state, timer_name, thought_process) MUST remain in English.
        
        OUTPUT JSON FORMAT:
        {
            "thought_process": "1. Analyze image (describe strictly what you see). 2. Compare to goal. 3. Formulate response.",
            "speech_output": "Text to speak (empty string if strictly monitoring with no update)",
            "status": "MONITORING_NO_CHANGE" | "INSTRUCTION_UPDATE" | "USER_INTERACTION",
            "next_state": "Exact string name of the next state",
            "timer_name": "Name (e.g. 'Pasta') or null",
            "timer_duration": "Seconds (int) or null"
        }

        ### STATE TRANSITION ENFORCEMENT (HIGHEST PRIORITY) ###
        1. You are a State Machine. You are currently in: '{current_state}'.
        2. You MUST select 'next_state' ONLY from this list: {valid_next_states}.
        3. DO NOT invent new states. DO NOT switch state if the user did not agree.

        ### VISUAL REASONING PROTOCOL (Use 'thought_process' for this) ###
        When asked to judge the state of food (e.g., cut size, doneness):
        1. FIRST, describe strictly what you see in the image (e.g., "I see a whole fillet," or "I see large chunks").
        2. THEN, compare it to the goal state.
        3. ONLY THEN give your verdict in 'speech_output'.

        INSTRUCTIONS:
        
        1. SETUP PHASE (States: START -> INGREDIENT_SCAN -> RECIPE_CONFIRMATION -> INSTRUCTION_OVERVIEW):
           - State: START
             * Goal: Greet and ask to see ingredients.
             * ACTION: If user says "Hi", "Ready" or agrees, MUST output "next_state": "INGREDIENT_SCAN" immediately. Do NOT wait for image here.
           - State: INGREDIENT_SCAN
             * Goal: Analyze image, identify ingredients, propose dish.
           - State: RECIPE_CONFIRMATION
             * Goal: Wait for agreement. Move to INSTRUCTION_OVERVIEW.
           - State: INSTRUCTION_OVERVIEW
             * Goal: List steps. Ask "Ready to cook?". Move to ACTIVE_COOKING.

        2. ACTIVE COOKING PHASE (State: ACTIVE_COOKING):
           
           ### SUB-CATEGORY 1: IF 'user_voice' IS EMPTY (Visual Monitoring Mode)
           1. CASE (General Monitoring): 
              CRITICAL: You are a PASSIVE OBSERVER. Do NOT check in. Do NOT ask if they are done.
              Even if the step looks finished visually, keep waiting.
              Output status "MONITORING_NO_CHANGE" and empty speech_output.
           2. CASE (Visual Mistake / Safety Hazard): 
              ONLY speak if the user is making a specific error (e.g., burning food, cutting dangerously).
              Explain the error clearly.

           ### SUB-CATEGORY 2: IF 'user_voice' IS NOT EMPTY (Interaction Mode)
           1. CASE (User says "Ok", "Got it", "Sure", "I see"):
              This is ACKNOWLEDGMENT. Do NOT move to the next step. 
              Output: "speech_output": "" (or very brief confirmation), "status": "MONITORING_NO_CHANGE".
           2. CASE (User explicitly confirms COMPLETION or asks for NEXT):
              (e.g., "I'm done", "Next step", "What's next?", "Ready").
              ACTION: Explain the NEXT step. Set status="INSTRUCTION_UPDATE".
           3. CASE (User asks visual judgment question): 
              (e.g. "Is it small enough?", "Is this done?").
              ACTION: Check 'thought_process' logic.
              - If the food looks exactly the same as the raw ingredient: Say "It doesn't look diced yet. It looks like a whole piece."
              - If you can't see it clearly: Say "I can't see the salmon clearly. Can you bring it closer?"
              - Only say "Yes" if you clearly see distinct small pieces.
           4. CASE (User asks general question):
              Answer the question. Remain on current step.

        3. GENERAL RULES:
           - "timer_name/duration": If user starts a timed task, provide details. Confirm verbally.
        """

        turn_context = {
            "current_state": current_state_obj.name,
            "goal": current_state_obj.description,
            "valid_next_states": current_state_obj.valid_next_states,
            "timestamp": datetime.now().isoformat(),
            "user_voice": user_voice,
            "image_provided": image_base64 is not None
        }

        user_message = [{"type": "text", "text": json.dumps(turn_context)}]
        if image_base64:
            user_message.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            })

        self.update_history("user", user_message)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": system_prompt}] + self.history,
            "response_format": {"type": "json_object"},
            "max_tokens": 500  # Increased to accommodate thought process
        }

        try:
            response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            data = response.json()
            if "error" in data: return None
            
            assistant_content = data['choices'][0]['message']['content']
            self.update_history("assistant", assistant_content)
            return json.loads(assistant_content)

        except Exception as e:
            print(f"Network/Parsing Error: {e}")
            return None

    def check_timers(self):
        """Checks if any local timers have expired."""
        now = time.time()
        expired = []
        for i in range(len(self.active_timers) - 1, -1, -1):
            timer = self.active_timers[i]
            if now >= timer['end_time']:
                expired.append(timer['name'])
                del self.active_timers[i]
        
        if expired:
            return f"[System Notification: The following timers have finished: {', '.join(expired)}. Please inform the user.]"
        return None

    def run(self):
        self.speak("系統啟動中 (System starting)")

        while True:
            state_obj = self.states[self.current_state_name]
            print(f"\n--- State: {state_obj.name} ---")
            
            user_voice = ""
            image_data = None
            timer_notification = None

            # --- LOGIC SPLIT: ACTIVE vs OTHERS ---

            if self.current_state_name == "ACTIVE_COOKING":
                # === PROACTIVE MODE ===
                # 1. Check Timers
                timer_notification = self.check_timers()
                if timer_notification:
                    print(f"⏰ {timer_notification}")
                    user_voice = timer_notification
                
                # 2. Listen (Wait up to 5s for voice)
                if not user_voice:
                    user_voice = self.listen(timeout=5)

                # 3. Capture Image (Fresh!)
                if state_obj.requires_image and not timer_notification:
                    print("📸 Monitoring Capture...")
                    image_data = self.capture_image()

            else:
                # === REACTIVE MODE (Wait for Input) ===
                print("🎤 Waiting for audio...")
                
                while True:
                    # Priority 1: Check Timers
                    timer_notification = self.check_timers()
                    if timer_notification:
                        print(f"⏰ {timer_notification}")
                        user_voice = timer_notification
                        break 
                    
                    # Priority 2: Check Voice
                    voice_input = self.listen(timeout=5)
                    if voice_input:
                        user_voice = voice_input
                        break 
                
                if state_obj.requires_image and not timer_notification:
                    print("📸 Reactive Capture...")
                    image_data = self.capture_image()

            # --- CALL API ---
            if not user_voice and not image_data and self.current_state_name == "START":
                continue 

            response = self.call_gpt_api(user_voice, image_data)
            if not response: continue

            # --- PROCESS RESPONSE ---
            thought = response.get("thought_process", "")
            speech = response.get("speech_output", "")
            status = response.get("status", "")
            next_state = response.get("next_state")
            timer_name = response.get("timer_name")
            timer_duration = response.get("timer_duration")

            # Debug: Print Thought Process to Console
            if thought:
                print(f"💭 Thought: {thought}")

            if timer_name and timer_duration:
                try:
                    duration_sec = int(timer_duration)
                    self.active_timers.append({"name": timer_name, "end_time": time.time() + duration_sec})
                    print(f"⏳ Timer Started: {timer_name} ({duration_sec}s)")
                except ValueError: pass

            if speech:
                self.speak(speech)
            elif status == "MONITORING_NO_CHANGE":
                print("✅ Monitoring... (No instructions needed)")

            if next_state in self.states:
                self.current_state_name = next_state
            elif next_state == "FINISHED":
                print("Cooking Session Complete.")
                break

        self.camera.release()

if __name__ == "__main__":
    if not API_KEY:
        print("Please set OPENAI_API_KEY environment variable.")
    else:
        bot = CookingAssistant(debug=True)
        bot.run()