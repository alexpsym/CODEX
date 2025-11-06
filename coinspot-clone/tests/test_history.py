import types
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Create a fake 'coinspot' module with ReadOnlyAPIV2 attribute
fake_module = types.ModuleType('coinspot')
fake_module.ReadOnlyAPIV2 = MagicMock()
sys.modules['coinspot'] = fake_module

# Ensure the project root is on the path so coinspot_history can be imported
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)

import coinspot_history

class TestFetchHistory(unittest.TestCase):
    def test_fetch_history_expected_keys(self):
        mock_api = MagicMock()
        mock_api.deposit_history.return_value = 'deposits'
        mock_api.withdrawal_history.return_value = 'withdrawals'
        mock_api.order_history.return_value = 'orders'
        mock_api.market_order_history.return_value = 'market_orders'
        mock_api.send_receive_history.return_value = 'send_receive'

        with patch('coinspot_history.ReadOnlyAPIV2', return_value=mock_api):
            result = coinspot_history.fetch_history()

        expected_keys = {
            'deposits',
            'withdrawals',
            'orders',
            'market_orders',
            'send_receive',
        }
        self.assertEqual(set(result.keys()), expected_keys)
        self.assertEqual(result['deposits'], 'deposits')
        self.assertEqual(result['withdrawals'], 'withdrawals')
        self.assertEqual(result['orders'], 'orders')
        self.assertEqual(result['market_orders'], 'market_orders')
        self.assertEqual(result['send_receive'], 'send_receive')


if __name__ == '__main__':
    unittest.main()
