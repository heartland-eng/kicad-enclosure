"""Test runner that ends with ``os._exit`` to avoid the OCP teardown segfault.

Usage (from the repo root)::

    python tests/run_tests.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

loader = unittest.TestLoader()
suite = loader.discover(os.path.join(ROOT, "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)

sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if result.wasSuccessful() else 1)
