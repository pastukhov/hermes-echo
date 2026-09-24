import unittest

from backend.common.error_codes import ErrorCode


class TestErrorCode(unittest.TestCase):
    def test_error_code_values(self):
        """Test all expected error codes present correct values."""
        expected_codes = {
            'AUDIO_RECEIVE_FAILED': 'audio_receive_failed',
            'AUDIO_INVALID': 'audio_invalid',
            'STT_FAILED': 'stt_failed',
            'HERMES_FAILED': 'hermes_failed',
            'HERMES_INVALID_RESPONSE': 'hermes_invalid_response',
            'AGENT_UNAVAILABLE': 'agent_unavailable',
            'AGENT_AUTH_REQUIRED': 'agent_auth_required',
            'AGENT_RATE_LIMITED': 'agent_rate_limited',
            'AGENT_TIMEOUT': 'agent_timeout',
            'AGENT_INVALID_RESPONSE': 'agent_invalid_response',
            'AGENT_PERMISSION_REQUIRED': 'permission_required',
            'AGENT_INTERRUPTED': 'interrupted',
            'NOTE_WRITE_FAILED': 'note_write_failed',
            'TTS_FAILED': 'tts_failed',
            'INTERNAL_ERROR': 'internal_error'
        }

        for name, value in expected_codes.items():
            with self.subTest(code=name):
                self.assertTrue(hasattr(ErrorCode, name))
                self.assertEqual(getattr(ErrorCode, name).value, value)

    def test_error_code_string_representation(self):
        """Test ErrorCode enum values converted string."""
        self.assertEqual(str(ErrorCode.AUDIO_RECEIVE_FAILED), 'audio_receive_failed')
        self.assertEqual(str(ErrorCode.HERMES_FAILED), 'hermes_failed')

    def test_error_code_enumeration(self):
        """Test iterate over all error codes."""
        codes = list(ErrorCode)
        self.assertEqual(len(codes), 15)
        values = [code.value for code in codes]
        expected_values = [
            'audio_receive_failed', 'audio_invalid', 'stt_failed', 'hermes_failed',
            'hermes_invalid_response', 'note_write_failed', 'tts_failed', 'internal_error',
            'agent_unavailable', 'agent_auth_required', 'agent_rate_limited',
            'agent_timeout', 'agent_invalid_response', 'permission_required', 'interrupted'
        ]
        self.assertCountEqual(values, expected_values)


if __name__ == '__main__':
    unittest.main()
