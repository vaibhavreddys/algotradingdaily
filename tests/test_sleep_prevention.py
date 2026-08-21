import unittest
import sys
from unittest.mock import patch, MagicMock
from live_trading.base_engine import (
    prevent_sleep_context,
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    ES_AWAYMODE_REQUIRED
)


class TestSleepPrevention(unittest.TestCase):
    def test_prevent_sleep_context_windows_mock(self):
        # Mock sys.platform to 'win32'
        mock_kernel32 = MagicMock()
        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        with patch('sys.platform', 'win32'):
            with patch.dict('sys.modules', {'ctypes': mock_ctypes}):
                with prevent_sleep_context():
                    # Check that SetThreadExecutionState was called on entry
                    mock_kernel32.SetThreadExecutionState.assert_called_with(
                        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
                    )
                # Check that SetThreadExecutionState was called on exit with ES_CONTINUOUS
                mock_kernel32.SetThreadExecutionState.assert_called_with(ES_CONTINUOUS)

    def test_prevent_sleep_context_non_windows_noop(self):
        # Mock sys.platform to 'linux'
        with patch('sys.platform', 'linux'):
            # Should enter and exit cleanly without raising any exceptions
            with prevent_sleep_context():
                pass


if __name__ == '__main__':
    unittest.main()
