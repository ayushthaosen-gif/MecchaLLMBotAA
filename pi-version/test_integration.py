import os, sys, time

os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("SIMULATE_SERVOS", "1")
os.environ.setdefault("WEATHER_LOCATION", "")  # forces the "not configured" path, no network call

sys.path.insert(0, os.path.dirname(__file__))
import brain

class FakeLLM:
    """Stand-in for a real Ollama/Claude backend so this test doesn't need
    a running model — it only exercises the Flask/memory/gesture wiring."""
    def chat(self, system_prompt, user_message):
        return f"(stub reply) heard: {user_message}"

brain.llm = FakeLLM()
client = brain.app.test_client()

def section(title):
    print(f"\n=== {title} ===")

section("1. /status before anything happens")
resp = client.get("/status")
print(resp.status_code, resp.get_json())

section("2. /chat with a wave-triggering message")
resp = client.post("/chat", json={"message": "hi there, can you wave hello?"})
print(resp.status_code, resp.get_json())

section("3. motion engine state right after the request returns")
print("busy:", brain.motion.is_busy)
time.sleep(0.3)
print("busy shortly after (gesture should be mid-flight):", brain.motion.is_busy)
time.sleep(3)
print("busy after gesture should have finished:", brain.motion.is_busy)

section("4. /chat with a weather-triggering message (no location configured)")
resp = client.post("/chat", json={"message": "what's the weather like today?"})
print(resp.status_code, resp.get_json())

section("5. /chat with an empty message (should be rejected)")
resp = client.post("/chat", json={"message": "  "})
print(resp.status_code, resp.get_json())

section("6. /status after activity")
resp = client.get("/status")
print(resp.status_code, resp.get_json())

section("7. persistent memory file contents")
print(brain.MEMORY_FILE.read_text())

section("8. servo calibration + smoothing sanity check (separate from Flask)")
from servo_controller import ServoBus, ServoBusConfig
from motion_engine import MotionEngine
bus2 = ServoBus(ServoBusConfig(simulate=True, servo_count=4, calibration_offsets={3: -5}))
engine2 = MotionEngine(bus2, max_speed_deg_per_sec=300)
engine2.start()
engine2.play("look_around")
time.sleep(2)
engine2.stop()
print("servo 3 logical angle after look_around:", bus2.get_angle(3))

print("\n=== DONE ===")
