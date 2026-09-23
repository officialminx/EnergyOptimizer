import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Keine mDNS-Ankündigung und keine Update-Abfrage aus den Tests heraus.
os.environ.setdefault("EO_MDNS", "0")
os.environ.setdefault("EO_UPDATE_CHECK", "0")
