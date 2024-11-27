import pytest
import numpy as np
from Main import EmergencyDispatcher
import re
import sounddevice as sd
import tempfile
import os
from unittest.mock import Mock, patch

@pytest.fixture(autouse=True)
def mock_flask_app():
    """Mock Flask app and dependencies"""
    with patch('Main.Flask'), \
         patch('Main.SocketIO'), \
         patch('Main.OpenAI'):
        yield

class TestEmergencyDispatcher:
    @pytest.fixture
    def dispatcher(self):
        """Fixture to create a fresh dispatcher instance for each test"""
        with patch('Main.OpenAI'):  # Mock OpenAI during initialization
            dispatcher = EmergencyDispatcher()
            yield dispatcher
            dispatcher.cleanup()

    @pytest.fixture
    def mock_audio_data(self):
        """Fixture to create mock audio data for testing"""
        return np.random.rand(1600).astype(np.int16)  # 100ms at 16kHz

    def test_audio_parameters(self, dispatcher):
        """Test audio configuration parameters"""
        assert dispatcher.sample_rate == 16000, "Sample rate should be 16kHz"
        assert dispatcher.channels == 1, "Should be mono channel"
        assert dispatcher.chunk_duration == 0.05, "Chunk duration should be 50ms"
        assert dispatcher.chunk_samples == 800, "Should have 800 samples per chunk"

    @pytest.mark.parametrize("text,expected_type", [
        ("There's a fire in the building!", "FIRE"),
        ("Someone is having a heart attack!", "MEDICAL"),
        ("There's a break-in in progress!", "POLICE"),
        ("My cat is stuck in a tree", None),  # Non-emergency
    ])
    def test_emergency_classification(self, text, expected_type):
        """Test emergency type detection from text"""
        emergency_patterns = {
            'MEDICAL': r'(heart attack|breathing|unconscious|bleeding|injury|injured|fell|fallen|seizure|stroke|choking|allergic|accident|overdose|pain|medical)',
            'FIRE': r'(fire|smoke|burning|flames|gas leak|explosion)',
            'POLICE': r'(break(-| )?in|robbery|theft|assault|weapon|gunshot|fight|domestic|violence|suspicious|burglary|stolen)'
        }

        detected_type = None
        for type_, pattern in emergency_patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                detected_type = type_
                break

        assert detected_type == expected_type, f"Emergency type detection failed for: {text}"

    @pytest.mark.parametrize("text,expected_address", [
        ("I'm at 123 Main Street, New York", "123 Main Street"),
        ("The location is 456 Park Avenue, Brooklyn", "456 Park Avenue"),
        ("No address mentioned here", None),
        ("At 789 Broadway Boulevard, Manhattan", "789 Broadway Boulevard"),
    ])
    def test_address_extraction(self, text, expected_address):
        """Test address extraction from text"""
        address_patterns = [
            r'at\s+([\d]+[\w\s,.-]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|Terr|Terrace)[\w\s,.-]+)',
            r'on\s+([\d]+[\w\s,.-]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|Terr|Terrace)[\w\s,.-]+)',
            r'(?:location|address|place) is\s+([\d]+[\w\s,.-]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|circle|cir|court|ct|way|parkway|pkwy|terr|terrace)[\w\s,.-]+)'
        ]

        found_address = None
        for pattern in address_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                found_address = match.group(1).strip()
                break

        if expected_address:
            assert found_address and expected_address in found_address, f"Address extraction failed for: {text}"
        else:
            assert found_address is None, f"False positive address detection in: {text}"

    def test_cleanup(self, dispatcher):
        """Test cleanup process"""
        # Create some temporary files
        temp_files = []
        for i in range(3):
            fd, path = tempfile.mkstemp(dir=dispatcher.temp_dir)
            os.close(fd)
            temp_files.append(path)

        # Run cleanup
        dispatcher.cleanup()

        # Verify cleanup
        assert not os.path.exists(dispatcher.temp_dir), "Temporary directory should be removed"
        for path in temp_files:
            assert not os.path.exists(path), f"Temporary file {path} should be removed"

    def test_audio_recording_state(self, dispatcher, mock_audio_data):
        """Test audio recording state management"""
        # Test initial state
        assert not dispatcher.is_recording
        assert len(dispatcher.speech_frames) == 0

        # Simulate speech detection
        dispatcher.detect_speech(mock_audio_data)
        assert dispatcher.speech_threshold > 0, "Speech threshold should be positive"

    @pytest.mark.parametrize("audio_length,expected_process", [
        (0.01, False),  # Too short
        (0.1, True),    # Long enough
        (1.0, True),    # Definitely long enough
    ])
    def test_minimum_audio_length(self, dispatcher, audio_length, expected_process):
        """Test minimum audio length requirements"""
        samples = int(audio_length * dispatcher.sample_rate)
        test_audio = np.random.rand(samples).astype(np.int16)
        
        with patch.object(dispatcher, 'handle_input') as mock_handle_input:
            dispatcher.speech_frames = [test_audio]
            dispatcher.process_recorded_speech()
            
            # Check if handle_input was called based on expected_process
            assert mock_handle_input.called == expected_process, \
                f"Audio processing for length {audio_length}s should {'not ' if not expected_process else ''}trigger handling"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])