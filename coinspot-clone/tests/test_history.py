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
        mock_api.deposit_history.return_value = {'deposits':[{'id':1}]}
        mock_api.withdrawal_history.return_value = {'withdrawals':[{'id':2}]}
        mock_api.order_history.return_value = {'buyorders':[{'id':3}], 'sellorders':[{'id':4}]}
        mock_api.market_order_history.return_value = {'buyorders':[{'id':5}], 'sellorders':[{'id':6}]}
        mock_api.send_receive_history.return_value = {'sendtransactions':[{'id':7}], 'receivetransactions':[{'id':8}]}

        with patch('coinspot_history.ReadOnlyAPIV2', return_value=mock_api):
            result = coinspot_history.fetch_history(api_key="dummy", api_secret="dummy")

        expected_keys = {
            'deposits',
            'withdrawals',
            'orders',
            'market_orders',
            'send_receive',
        }
        self.assertEqual(set(result.keys()), expected_keys)
        self.assertEqual(len(result['deposits']), 1)
        self.assertEqual(len(result['withdrawals']), 1)
        self.assertEqual(len(result['orders']), 2)
        self.assertEqual(len(result['market_orders']), 2)
        self.assertEqual(len(result['send_receive']), 2)


if __name__ == '__main__':
    unittest.main()
