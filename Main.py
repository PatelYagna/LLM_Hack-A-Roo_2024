from flask import Flask, render_template_string
from flask_socketio import SocketIO
import sounddevice as sd
import numpy as np
import threading
import time
from openai import OpenAI
import wave
import os
import tempfile

app = Flask(__name__)
socketio = SocketIO(app)

class EmergencyDispatcher:
    def __init__(self):
        self.client = OpenAI(api_key="Open API Key Here")
        self.assistant_id = "asst_DGcJujd3wtjBRZ4KsdrD0q5X"
        self.thread = self.client.beta.threads.create()
        
        self.sample_rate = 16000
        self.chunk_duration = 0.05
        self.speech_threshold = 700
        self.silence_duration = 1.5
        self.speech_frames, self.silence_frames = [], 0
        self.is_recording, self.call_in_progress = False, True
        self.temp_dir = tempfile.mkdtemp()

    def detect_speech(self, audio_data):
        return np.abs(audio_data).mean() > self.speech_threshold

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")
        
        if self.detect_speech(indata):
            self.is_recording, self.silence_frames = True, 0
            self.speech_frames.append(indata.copy())
        elif self.is_recording:
            self.silence_frames += 1
            self.speech_frames.append(indata.copy())
            if self.silence_frames * self.chunk_duration >= self.silence_duration:
                self.process_recorded_speech()
                self.is_recording, self.speech_frames, self.silence_frames = False, [], 0

    def record_and_process(self):
        with sd.InputStream(channels=1, samplerate=self.sample_rate, blocksize=int(self.sample_rate * self.chunk_duration), callback=self.audio_callback, dtype=np.int16):
            print("Listening for speech...")
            while self.call_in_progress:
                time.sleep(0.1)

    def process_recorded_speech(self):
        if not self.speech_frames:
            return
        
        audio_data = np.concatenate(self.speech_frames)
        if len(audio_data) / self.sample_rate < 0.05:
            return
        
        temp_path = os.path.join(self.temp_dir, f"speech_{time.time()}.wav")
        with wave.open(temp_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())

        with open(temp_path, 'rb') as audio_file:
            transcript = self.client.audio.transcriptions.create(model="whisper-1", file=audio_file, response_format="text").strip()
        os.remove(temp_path)
        
        if transcript:
            print(f"Caller: {transcript}")
            socketio.emit('transcript_update', {'role': 'caller', 'message': transcript, 'timestamp': time.strftime('%H:%M:%S')})
            self.handle_ai_response(transcript)

    def handle_ai_response(self, text):
        try:
            self.client.beta.threads.messages.create(thread_id=self.thread.id, role="user", content=text)
            run = self.client.beta.threads.runs.create(thread_id=self.thread.id, assistant_id=self.assistant_id)

            start_time = time.time()
            while time.time() - start_time < 30:
                run_status = self.client.beta.threads.runs.retrieve(thread_id=self.thread.id, run_id=run.id)
                if run_status.status == 'completed':
                    for msg in self.client.beta.threads.messages.list(thread_id=self.thread.id).data:
                        if msg.role == "assistant":
                            response = msg.content[0].text.value
                            print(f"Dispatcher: {response}")
                            socketio.emit('transcript_update', {'role': 'dispatcher', 'message': response, 'timestamp': time.strftime('%H:%M:%S')})
                            return
                time.sleep(0.5)
        except Exception as e:
            print(f"Error handling AI response: {e}")
            socketio.emit('transcript_update', {'role': 'dispatcher', 'message': "I'm experiencing technical difficulties. Please hold.", 'timestamp': time.strftime('%H:%M:%S')})

    def run(self):
        self.record_and_process()

    def cleanup(self):
        self.call_in_progress = False
        for file in os.listdir(self.temp_dir):
            os.remove(os.path.join(self.temp_dir, file))
        os.rmdir(self.temp_dir)

@app.route('/')
def home():
    return render_template_string("<h1>Emergency Dispatch System</h1>")

@socketio.on('start_call')
def handle_start_call():
    global dispatcher
    dispatcher = EmergencyDispatcher()
    threading.Thread(target=dispatcher.run, daemon=True).start()

@socketio.on('end_call')
def handle_end_call():
    if hasattr(dispatcher, 'cleanup'):
        dispatcher.cleanup()

if __name__ == "__main__":
    socketio.run(app, debug=True)