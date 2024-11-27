from flask import Flask, render_template_string, jsonify
from flask_socketio import SocketIO
import sounddevice as sd
import numpy as np
import threading
import queue
import time
from openai import OpenAI
import wave
import os
import tempfile

#pip install flask
#pip install flask flask-socketio
#pip install sounddevice
#pip install openai
#pip install numpy
#pip install wave

app = Flask(__name__)
socketio = SocketIO(app)

# Your existing EmergencyDispatcher class here
class EmergencyDispatcher:
    def __init__(self):
        # Initialize OpenAI client
        self.client = OpenAI(api_key="Open API Key Here")
        self.assistant_id = "asst_DGcJujd3wtjBRZ4KsdrD0q5X"
        self.thread = self.client.beta.threads.create()
        
        # Audio parameters
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_duration = 0.05  # 100ms chunks for speech detection
        self.chunk_samples = int(self.sample_rate * self.chunk_duration)
        
        # Speech detection parameters
        self.speech_threshold = 700  # Adjust based on your microphone
        self.silence_duration = 1.5  # Seconds of silence to end recording
        self.min_audio_length = 0.05  # Minimum audio length to process
        self.speech_frames = []
        self.silence_frames = 0
        self.is_recording = False
        
        # State management
        self.call_in_progress = True
        self.temp_dir = tempfile.mkdtemp()
        self.current_address = None

    def detect_speech(self, audio_data):
        """Detect if audio contains speech using amplitude threshold."""
        return np.abs(audio_data).mean() > self.speech_threshold

    def record_and_process(self):
        """Continuously record and process audio with speech detection."""
        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"Audio status: {status}")
            
            # Check for speech in current chunk
            if self.detect_speech(indata):
                if not self.is_recording:
                    print("Speech detected - starting recording...")
                    self.is_recording = True
                self.speech_frames.append(indata.copy())
                self.silence_frames = 0
            elif self.is_recording:
                self.silence_frames += 1
                self.speech_frames.append(indata.copy())  # Keep some silence for natural speech
                
                # Check if silence duration exceeded
                silence_time = self.silence_frames * self.chunk_duration
                if silence_time >= self.silence_duration:
                    print("Silence detected - processing speech...")
                    self.process_recorded_speech()
                    self.is_recording = False
                    self.speech_frames = []
                    self.silence_frames = 0

        try:
            with sd.InputStream(
                channels=self.channels,
                samplerate=self.sample_rate,
                blocksize=self.chunk_samples,
                callback=audio_callback,
                dtype=np.int16
            ):
                print("Listening for speech...")
                while self.call_in_progress:
                    time.sleep(0.1)
        except Exception as e:
            print(f"Error in audio stream: {e}")

    def process_recorded_speech(self):
        """Process the recorded speech frames."""
        if not self.speech_frames:
            return

        try:
            # Combine all frames
            audio_data = np.concatenate(self.speech_frames)
            duration = len(audio_data) / self.sample_rate

            # Only process if audio is long enough
            if duration >= self.min_audio_length:
                # Save to temporary WAV file
                temp_path = os.path.join(self.temp_dir, f"speech_{time.time()}.wav")
                with wave.open(temp_path, 'wb') as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(audio_data.tobytes())

                # Transcribe
                with open(temp_path, 'rb') as audio_file:
                    transcript = self.client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="text"
                    )

                os.remove(temp_path)

                if transcript and transcript.strip():
                    print(f"Caller: {transcript}")
                    self.handle_input(transcript)

        except Exception as e:
            print(f"Error processing recorded speech: {e}")

    def text_to_speech(self, text):
        """Convert text to speech using OpenAI's TTS."""
        try:
            temp_path = os.path.join(self.temp_dir, f"response_{time.time()}.mp3")
            
            response = self.client.audio.speech.create(
                model="tts-1",
                voice="shimmer",
                input=text
            )
            
            response.stream_to_file(temp_path)
            
            if os.path.exists(temp_path):
                if os.name == 'posix':
                    os.system(f"afplay '{temp_path}'")
                elif os.name == 'nt':
                    os.system(f'start "" "{temp_path}"')
                os.remove(temp_path)
                
        except Exception as e:
            print(f"Text-to-speech error: {e}")

    def handle_input(self, text):
        """Handle transcribed input and get AI response."""
        if not text:
            return

        try:
            message = self.client.beta.threads.messages.create(
                thread_id=self.thread.id,
                role="user",
                content=text
            )

            run = self.client.beta.threads.runs.create(
                thread_id=self.thread.id,
                assistant_id=self.assistant_id
            )

            start_time = time.time()
            while time.time() - start_time < 30:
                run_status = self.client.beta.threads.runs.retrieve(
                    thread_id=self.thread.id,
                    run_id=run.id
                )
                if run_status.status == 'completed':
                    messages = self.client.beta.threads.messages.list(
                        thread_id=self.thread.id
                    )
                    
                    for msg in messages.data:
                        if msg.role == "assistant":
                            response = msg.content[0].text.value
                            print(f"Dispatcher: {response}")
                            self.text_to_speech(response)
                            return
                time.sleep(0.5)

        except Exception as e:
            print(f"Error handling input: {e}")
            self.text_to_speech("I'm experiencing technical difficulties. Please hold.")

        address_patterns = [
            r'at\s+([\d]+[\w\s,.-]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|Terr|Terrace)[\w\s,.-]+)',
            r'on\s+([\d]+[\w\s,.-]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|Terr|Terrace)[\w\s,.-]+)',
            r'(?:location|address|place) is\s+([\d]+[\w\s,.-]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|terr|terrace)[\w\s,.-]+)'
        ]
        
        for pattern in address_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.current_address = match.group(1)
                break

    def run(self):
        """Main method to run the dispatcher."""
        try:
            # Initial greeting
            self.text_to_speech("911, what's your emergency?")
            
            # Start recording and processing
            self.record_and_process()

        except KeyboardInterrupt:
            print("\nEmergency dispatcher shutting down...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources."""
        self.call_in_progress = False
        try:
            for file in os.listdir(self.temp_dir):
                os.remove(os.path.join(self.temp_dir, file))
            os.rmdir(self.temp_dir)
        except Exception as e:
            print(f"Error cleaning up: {e}")
    
    def handle_input(self, text):
        """Modified to emit updates to frontend"""
        if not text:
            return

        try:
            # Emit transcript update
            socketio.emit('transcript_update', {
                'role': 'caller',
                'message': text,
                'timestamp': time.strftime('%H:%M:%S')
            })

            message = self.client.beta.threads.messages.create(
                thread_id=self.thread.id,
                role="user",
                content=text
            )

            run = self.client.beta.threads.runs.create(
                thread_id=self.thread.id,
                assistant_id=self.assistant_id
            )

            start_time = time.time()
            while time.time() - start_time < 30:
                run_status = self.client.beta.threads.runs.retrieve(
                    thread_id=self.thread.id,
                    run_id=run.id
                )
                if run_status.status == 'completed':
                    messages = self.client.beta.threads.messages.list(
                        thread_id=self.thread.id
                    )
                    
                    for msg in messages.data:
                        if msg.role == "assistant":
                            response = msg.content[0].text.value
                            print(f"Dispatcher: {response}")
                            
                            # Emit dispatcher response
                            socketio.emit('transcript_update', {
                                'role': 'dispatcher',
                                'message': response,
                                'timestamp': time.strftime('%H:%M:%S')
                            })
                            
                            self.text_to_speech(response)
                            return
                time.sleep(0.5)

        except Exception as e:
            print(f"Error handling input: {e}")
            self.text_to_speech("I'm experiencing technical difficulties. Please hold.")

# HTML template for the frontend
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Emergency Dispatch System</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/axios/0.21.1/axios.min.js"></script>
    <style>
        /* Previous styles remain the same */
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            margin-top: 20px;
        }
        .section {
            border: 1px solid #ccc;
            padding: 15px;
            border-radius: 5px;
            background: white;
        }
        .transcript {
            height: 300px;
            overflow-y: auto;
        }
        .emergency-button {
            background-color: #ff4444;
            color: white;
            padding: 15px 30px;
            border: none;
            border-radius: 5px;
            font-size: 18px;
            cursor: pointer;
            display: block;
            margin: 0 auto;
            transition: background-color 0.3s;
        }
        .emergency-button:hover {
            background-color: #cc0000;
        }
        .emergency-button.active {
            background-color: #cc0000;
        }
        .message {
            margin: 10px 0;
            padding: 5px;
        }
        .timestamp {
            color: #666;
            font-size: 0.8em;
        }
        .dispatcher {
            color: blue;
        }
        .caller {
            color: green;
        }
        #map {
            height: 300px;
            width: 100%;
            border-radius: 5px;
        }
        .status-emergency {
            background-color: #ffebee;
            color: #c62828;
            padding: 10px;
            border-radius: 5px;
            font-weight: bold;
            animation: pulse 2s infinite;
        }
        .status-active {
            background-color: #e8f5e9;
            color: #2e7d32;
            padding: 10px;
            border-radius: 5px;
        }
        .ai-summary {
            background-color: #f5f5f5;
            padding: 10px;
            border-radius: 5px;
            margin-top: 10px;
        }
        @keyframes pulse {
            0% { background-color: #ffebee; }
            50% { background-color: #ffcdd2; }
            100% { background-color: #ffebee; }
        }
    </style>
</head>
<body>
    <button id="emergencyButton" class="emergency-button">Start Emergency Call</button>
    
    <div class="grid">
        <div class="section">
            <h2>Transcript</h2>
            <div id="transcript" class="transcript"></div>
        </div>
        
        <div class="section">
            <h2>AI Summary</h2>
            <div id="aiSummary" class="ai-summary">Waiting for emergency details...</div>
        </div>
        
        <div class="section">
            <h2>Location</h2>
            <div id="map"></div>
        </div>
        
        <div class="section">
            <h2>Dispatch Status</h2>
            <div id="dispatchStatus" class="status-active">Standby</div>
        </div>
    </div>

    <script>
        const socket = io();
        let callActive = false;
        let map;
        let marker;
        let emergencyType = null;
        let dispatchedUnits = new Set();
        let emergencySummary = {
            type: null,
            location: null,
            problem: null,
            victim_status: null,
            key_details: new Set()
        };
        
        // Initialize map
        function initMap() {
            map = L.map('map').setView([40.7128, -74.0060], 13);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors'
            }).addTo(map);
        }

        // Address detection
        function findAddress(text) {
            // More comprehensive address patterns
            const addressPatterns = [
                // Standard street address with city/state
                /(?:at|on|near)\s+(\d+[\w\s]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|terrace|terr)[\w\s,.-]*(?:,\s*[\w\s]+,\s*[A-Z]{2})?)/i,
                
                // Location/address is format
                /(?:location|address|place)\s+(?:is|at)\s+(\d+[\w\s]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|terrace|terr)[\w\s,.-]*(?:,\s*[\w\s]+,\s*[A-Z]{2})?)/i,
                
                // Direct address mention
                /(\d+[\w\s]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|terrace|terr)[\w\s,.-]*(?:,\s*[\w\s]+,\s*[A-Z]{2})?)/i
            ];

            let fullAddress = null;
            
            // Try to find the address
            for (let pattern of addressPatterns) {
                const match = text.match(pattern);
                if (match) {
                    fullAddress = match[1].trim();
                    break;
                }
            }

            // If we found an address, try to extract city and state
            if (fullAddress) {
                // Try to find city and state if not already included
                const cityStatePattern = /(?:in|at)\s+([\w\s]+),\s*([A-Z]{2})/i;
                const cityStateMatch = text.match(cityStatePattern);
                
                if (cityStateMatch && !fullAddress.includes(cityStateMatch[1])) {
                    // Append city and state if they're not already in the address
                    fullAddress += `, ${cityStateMatch[1]}, ${cityStateMatch[2]}`;
                } else if (!fullAddress.includes(',')) {
                    // If no city/state found and not in address, append default (New York, NY)
                    fullAddress += ', New York, NY';
                }
            }

            return fullAddress;
        }

        // Improved geocoding function
        async function updateMapWithAddress(address) {
            try {
                // First try with the full address
                let response = await axios.get(`https://nominatim.openstreetmap.org/search`, {
                    params: {
                        q: address,
                        format: 'json',
                        limit: 1,
                        addressdetails: 1
                    },
                    headers: {
                        'User-Agent': 'Emergency Dispatch System'
                    }
                });

                if (!response.data.length) {
                    // If no results, try with just the street address part
                    const streetAddress = address.split(',')[0];
                    response = await axios.get(`https://nominatim.openstreetmap.org/search`, {
                        params: {
                            q: streetAddress + ', New York, NY',  // Append New York by default
                            format: 'json',
                            limit: 1,
                            addressdetails: 1
                        },
                        headers: {
                            'User-Agent': 'Emergency Dispatch System'
                        }
                    });
                }

                if (response.data && response.data.length > 0) {
                    const { lat, lon } = response.data[0];
                    
                    // Convert to numbers and check if they're valid
                    const latitude = parseFloat(lat);
                    const longitude = parseFloat(lon);
                    
                    if (!isNaN(latitude) && !isNaN(longitude)) {
                        // Update map view
                        map.setView([latitude, longitude], 18);  // Increased zoom level for better detail
                        
                        // Remove existing marker if any
                        if (marker) {
                            marker.remove();
                        }
                        
                        // Add new marker with pulse animation
                        const pulsingIcon = L.divIcon({
                            className: 'pulsing-marker',
                            html: '<div class="pulse"></div>',
                            iconSize: [20, 20]
                        });
                        
                        marker = L.marker([latitude, longitude], {
                            icon: pulsingIcon
                        }).addTo(map);
                        
                        // Add a circle to show approximate area
                        L.circle([latitude, longitude], {
                            color: 'red',
                            fillColor: '#f03',
                            fillOpacity: 0.2,
                            radius: 50
                        }).addTo(map);
                        
                        // Add popup with address information
                        marker.bindPopup(`
                            <strong>Emergency Location</strong><br>
                            ${address}<br>
                            <small>Lat: ${latitude.toFixed(6)}<br>Long: ${longitude.toFixed(6)}</small>
                        `).openPopup();
                        
                        // Update emergencySummary with precise location
                        emergencySummary.location = address;
                        emergencySummary.coordinates = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
                    }
                }
            } catch (error) {
                console.error('Error geocoding address:', error);
            }
        }

        // Add CSS for pulsing marker
        const style = document.createElement('style');
        style.textContent = `
            .pulsing-marker {
                position: relative;
            }
            
            .pulse {
                display: block;
                width: 20px;
                height: 20px;
                border-radius: 50%;
                background: #ff3b30;
                border: 2px solid #fff;
                cursor: pointer;
                box-shadow: 0 0 0 rgba(255, 59, 48, 0.4);
                animation: pulse 2s infinite;
            }
            
            @keyframes pulse {
                0% {
                    box-shadow: 0 0 0 0 rgba(255, 59, 48, 0.4);
                }
                70% {
                    box-shadow: 0 0 0 20px rgba(255, 59, 48, 0);
                }
                100% {
                    box-shadow: 0 0 0 0 rgba(255, 59, 48, 0);
                }
            }
        `;
        document.head.appendChild(style);

        // Emergency type detection with problem identification
        function detectEmergencyType(text) {
            const emergencyPatterns = {
                'MEDICAL': {
                    pattern: /(heart attack|breathing|unconscious|bleeding|injury|injured|fell|fallen|seizure|stroke|choking|allergic|accident|overdose|pain|medical)/i,
                    problems: {
                        'CHOKING': /choking/i,
                        'HEART_ATTACK': /heart attack/i,
                        'BREATHING': /(?:difficulty |trouble |can't |not |heavy )breathing/i,
                        'UNCONSCIOUS': /unconscious|passed out/i,
                        'BLEEDING': /bleeding/i,
                        'INJURY': /injury|injured|fell|fallen/i
                    }
                },
                'FIRE': {
                    pattern: /(fire|smoke|burning|flames|gas leak|explosion)/i,
                    problems: {
                        'STRUCTURE_FIRE': /building|house|apartment|structure|room on fire/i,
                        'GAS_LEAK': /gas leak/i,
                        'EXPLOSION': /explosion/i
                    }
                },
                'POLICE': {
                    pattern: /(break(-| )?in|robbery|theft|assault|weapon|gunshot|fight|domestic|violence|suspicious|burglary|stolen)/i,
                    problems: {
                        'BREAK_IN': /break(-| )?in|burglary/i,
                        'ASSAULT': /assault|fight|violence/i,
                        'WEAPON': /weapon|gunshot|gun|knife/i
                    }
                }
            };

            for (let [type, data] of Object.entries(emergencyPatterns)) {
                if (data.pattern.test(text)) {
                    // Check for specific problems
                    for (let [problem, pattern] of Object.entries(data.problems)) {
                        if (pattern.test(text)) {
                            emergencySummary.problem = problem.replace(/_/g, ' ');
                            return type;
                        }
                    }
                    return type;
                }
            }
            return null;
        }

        // Update AI summary
        function updateAISummary(data) {
            const type = detectEmergencyType(data.message);
            if (type) emergencySummary.type = type;
            
            const address = findAddress(data.message);
            if (address) emergencySummary.location = address;

            // Victim status detection
            const victimStatusMatch = data.message.match(/(conscious|unconscious|breathing|not breathing|responsive|unresponsive|bleeding|stable|critical|awake|alert|confused|dizzy)/i);
            if (victimStatusMatch) {
                emergencySummary.victim_status = victimStatusMatch[0];
            }

            // Key details detection
            const keyDetailPatterns = [
                /multiple victims/i,
                /weapon present/i,
                /children involved/i,
                /elderly person/i,
                /heavy smoke/i,
                /spreading quickly/i
            ];

            keyDetailPatterns.forEach(pattern => {
                const match = data.message.match(pattern);
                if (match) {
                    emergencySummary.key_details.add(match[0]);
                }
            });

            // Generate summary HTML
            let summaryHTML = '<div class="ai-summary">';
            if (emergencySummary.type) summaryHTML += `<strong>Type:</strong> ${emergencySummary.type}<br>`;
            if (emergencySummary.problem) summaryHTML += `<strong>Problem:</strong> ${emergencySummary.problem}<br>`;
            if (emergencySummary.location) summaryHTML += `<strong>Location:</strong> ${emergencySummary.location}<br>`;
            if (emergencySummary.victim_status) summaryHTML += `<strong>Status:</strong> ${emergencySummary.victim_status}<br>`;
            
            if (emergencySummary.key_details.size > 0) {
                summaryHTML += '<strong>Key Details:</strong><ul>';
                emergencySummary.key_details.forEach(detail => {
                    summaryHTML += `<li>${detail}</li>`;
                });
                summaryHTML += '</ul>';
            }
            summaryHTML += '</div>';

            document.getElementById('aiSummary').innerHTML = summaryHTML;
        }

        // Update dispatch status
        function updateDispatchStatus(text) {
            const type = detectEmergencyType(text);
            if (type && !emergencyType) {
                emergencyType = type;
            }

            if (emergencyType) {
                switch (emergencyType) {
                    case 'MEDICAL':
                        dispatchedUnits.add('🚑 Ambulance');
                        if (text.match(/critical|severe|unconscious|not breathing/i)) {
                            dispatchedUnits.add('🚁 Medical Helicopter');
                        }
                        break;
                    case 'FIRE':
                        dispatchedUnits.add('🚒 Fire Engine');
                        dispatchedUnits.add('🚑 Ambulance (Standby)');
                        if (text.match(/large|spreading|building|structure/i)) {
                            dispatchedUnits.add('🚒 Additional Fire Units');
                        }
                        break;
                    case 'POLICE':
                        dispatchedUnits.add('🚓 Police Units');
                        if (text.match(/weapon|gun|knife|violent|assault/i)) {
                            dispatchedUnits.add('🚨 SWAT Team');
                        }
                        break;
                }

                const statusHTML = `
                    <div class="status-emergency">
                        <span style="font-size: 1.2em">🚨 ${emergencyType} EMERGENCY IN PROGRESS 🚨</span><br>
                        <strong>Dispatched Units:</strong><br>
                        ${Array.from(dispatchedUnits).map(unit => `• ${unit}`).join('<br>')}
                        ${emergencySummary.location ? `<br><strong>Location:</strong> ${emergencySummary.location}` : ''}
                    </div>
                `;
                document.getElementById('dispatchStatus').innerHTML = statusHTML;
            }
        }

        // Socket event handlers
        socket.on('transcript_update', function(data) {
            // Update transcript
            const transcript = document.getElementById('transcript');
            const message = document.createElement('div');
            message.className = 'message';
            message.innerHTML = `
                <span class="timestamp">${data.timestamp}</span>
                <br>
                <span class="${data.role}">${data.role}: ${data.message}</span>
            `;
            transcript.appendChild(message);
            transcript.scrollTop = transcript.scrollHeight;
            
            // Check for address
            const address = findAddress(data.message);
            if (address) {
                emergencySummary.location = address;
                updateMapWithAddress(address);
            }
            
            // Update other components
            updateDispatchStatus(data.message);
            updateAISummary(data);
        });

        // Initialize everything when page loads
        window.onload = function() {
            initMap();
        };

        // Emergency button handler
        document.getElementById('emergencyButton').addEventListener('click', function() {
            callActive = !callActive;
            this.textContent = callActive ? 'End Emergency Call' : 'Start Emergency Call';
            this.classList.toggle('active');
            
            if (callActive) {
                socket.emit('start_call');
                document.getElementById('dispatchStatus').innerHTML = '<div class="status-active">Call Active - Awaiting Details</div>';
                // Reset all tracking variables
                emergencyType = null;
                dispatchedUnits.clear();
                emergencySummary = {
                    type: null,
                    location: null,
                    problem: null,
                    victim_status: null,
                    key_details: new Set()
                };
                if (marker) marker.remove();
                map.setView([40.7128, -74.0060], 13);
            } else {
                socket.emit('end_call');
                document.getElementById('dispatchStatus').innerHTML = '<div class="status-active">Call Ended</div>';
            }
        });
    </script>
</body>
</html>
"""

# Flask routes
@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('start_call')
def handle_start_call():
    global dispatcher
    dispatcher = EmergencyDispatcher()
    thread = threading.Thread(target=dispatcher.run)
    thread.daemon = True
    thread.start()

@socketio.on('end_call')
def handle_end_call():
    if hasattr(dispatcher, 'cleanup'):
        dispatcher.cleanup()

if __name__ == "__main__":
    socketio.run(app, debug=True)
