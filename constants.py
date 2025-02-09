import pytz

# File paths
USER_FILE = "users.json"
KEY_FILE = "keys.json"

# Attack settings
DEFAULT_THREADS = 699
MAX_DURATION = 300  # Maximum attack duration in seconds
COOLDOWN_TIME = 240  # Cooldown between attacks for non-admin users
REQUEST_TIMEOUT = 10  # Timeout for HTTP requests
MAX_CONCURRENT_ATTACKS = 3  # Maximum number of concurrent attacks per user
MAX_ATTACKS_PER_IP = 1

# Key settings
MAX_KEYS_PER_ADMIN = 50  # Maximum number of keys an admin can generate per day
KEY_LENGTH = 10  # Key length

# Time settings
IST_TZ = pytz.timezone('Asia/Kolkata')  # Indian Standard Time zone

# Rate limiting
RATE_LIMIT_WINDOW = 60  # seconds
MAX_COMMANDS_PER_WINDOW = 10