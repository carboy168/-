try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # Environment variables still work in minimal/offline test runtimes.
    def _load_dotenv(*args, **kwargs):
        return False

load_dotenv = _load_dotenv
