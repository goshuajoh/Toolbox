"""Run every test:  python run_tests.py   (needs pandas and openpyxl; standard unittest, no pytest)."""
import os
import sys
import unittest

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "tests"))
suite = unittest.defaultTestLoader.discover(os.path.join(here, "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
