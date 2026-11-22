"""The two tools themselves must run on the standard library alone; the probe files must be current."""
import os
import subprocess
import sys
import unittest

from helpers import ROOT


class Packaging(unittest.TestCase):
    def test_tools_import_no_third_party_module(self):
        code = ("import sys; import ayx2py.__main__, ayx2py.codegen, ayx2py.spec, vba2py.__main__, vba2py.analyse, vba2py.ovba;"
                "bad = [m for m in ('pandas', 'numpy', 'openpyxl') if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_probe_files_are_current(self):
        sys.path.insert(0, os.path.join(ROOT, "probe"))
        import make_probe
        for name, text in make_probe.main(write=False).items():
            with open(os.path.join(ROOT, "probe", name), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), text, name + " is stale: run python probe/make_probe.py")


if __name__ == "__main__":
    unittest.main()
