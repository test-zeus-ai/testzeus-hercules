from testzeus_hercules import core  # type: ignore # noqa: F401

# Apply NLTK security patch early to prevent zip slip vulnerability
# CVE: Critical vulnerability in NLTK <=3.9.2
from testzeus_hercules.utils.nltk_security_patcher import install_nltk_security_patcher
install_nltk_security_patcher()

# NOTE: diskcache CVE-2025-69872 (pickle RCE) - JSONDisk patch reverted because
# autogen caches ChatCompletion objects which are not JSON-serializable.
# Mitigation: restrict write access to cache dirs (e.g. .cache, opt/log_files).