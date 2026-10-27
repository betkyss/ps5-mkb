import copy
import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from dashboard.config import load_connection, validate_connection
from dashboard.schema import Problem


class ConnectionConfigTests(unittest.TestCase):
    def setUp(self):
        self.value = dict(console_name='Test console', console_host='192.0.2.10',
                          mouse=dict(vendor_id=0x1234, product_id=0x5678, location_id=0x1000000))

    def test_returns_independent_canonical_config(self):
        result = validate_connection(self.value)
        result['mouse']['vendor_id'] = 1
        self.assertEqual(self.value['mouse']['vendor_id'], 0x1234)

    def test_rejects_incomplete_and_invalid_values(self):
        cases = [None, {}, dict(self.value, extra=True)]
        for field, values in [('console_name', ['', '-option', ' trailing ', 'line\nbreak']),
                              ('console_host', ['host.local', '0.0.0.0', '224.0.0.1', '::1', 42])]:
            cases.extend(dict(self.value, **{field: value}) for value in values)
        for key in self.value['mouse']:
            for value in (True, 0, -1, '123', 0x100000000):
                candidate = copy.deepcopy(self.value)
                candidate['mouse'][key] = value
                cases.append(candidate)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(Problem):
                validate_connection(case)

    def test_missing_and_invalid_file_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(Problem, 'configure.py'):
                load_connection(directory)
            Path(directory, 'connection.json').write_text('{}')
            with self.assertRaises(Problem):
                load_connection(directory)

    def test_mouse_discovery_filters_and_deduplicates(self):
        spec = importlib.util.spec_from_file_location('configure', Path(__file__).resolve().parents[1] / 'scripts/configure.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mouse = dict(PrimaryUsagePage=1, PrimaryUsage=2, VendorID=1, ProductID=2,
                     LocationID=3, Product='Test mouse')
        keyboard = dict(mouse, PrimaryUsage=6, LocationID=4)
        missing_location = {k: v for k, v in mouse.items() if k != 'LocationID'}
        data = plistlib.dumps([{'IORegistryEntryChildren': [mouse, mouse, keyboard, missing_location]}])
        with patch.object(module.subprocess, 'check_output', return_value=data):
            self.assertEqual(module.mice(), [dict(name='Test mouse', vendor_id=1, product_id=2, location_id=3)])


if __name__ == '__main__':
    unittest.main()
