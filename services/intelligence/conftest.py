"""Test-environment defaults for the intelligence service.

The service now fails closed: anonymous HTTP access and unrestricted local path
resolution both require an explicit opt-in AND a local environment (audit
findings C-3 and C-4). The test suite is such a local environment, so it opts in
here rather than each test reconfiguring the process.

`setdefault` is used throughout so an explicitly-set environment variable — for
example a test that exercises the production posture — still wins.
"""
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
os.environ.setdefault("ARGUS_ALLOW_UNRESTRICTED_LOCAL_PATHS", "true")
