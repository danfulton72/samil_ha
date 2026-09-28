"""Constants for the Samil Power integration."""

DOMAIN = "samil"

CONF_INTERFACE = "interface"
CONF_INVERTER_HOST = "inverter_host"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 10  # seconds
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 300

# While the inverter is off (night), only search for it this often.
OFFLINE_RETRY_INTERVAL = 60  # seconds

# Stored in the config entry at setup
DATA_MODEL = "model"
