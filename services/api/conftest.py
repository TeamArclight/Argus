import os

# Set a deterministic, valid test-only secret for pytest suite before any app modules import Settings
os.environ.setdefault("ARGUS_JWT_SECRET", "test-secret-key-must-be-at-least-32-chars-long-001")
