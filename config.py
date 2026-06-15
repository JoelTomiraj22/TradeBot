import os

BINANCE_API_KEY = os.environ.get(
    "BINANCE_API_KEY",
    "CBqIbd1Ln8B4n4yygJBlAXMYkHCYd5xTYYpF7rjoRyaODDeh9mAdgvfxosqzWenz",
)
BINANCE_API_SECRET = os.environ.get(
    "BINANCE_API_SECRET",
    "k3h5YhSXvSIW7TPgCUx0fPlVVSz1DMjc6vOk7XPEP8tnQU5CiV0CBiXBgCGqPtVv",
)
BINANCE_TESTNET = True
BASE_URL = "https://testnet.binancefuture.com"

SIGNALS_FILE = os.path.join(os.path.dirname(__file__), "signals.json")
POLL_INTERVAL = 2  # seconds between signal file checks
