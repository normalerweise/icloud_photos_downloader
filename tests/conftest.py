"""Root conftest: pin timezone to UTC so tests are independent of the host."""

import os
import time

os.environ["TZ"] = "UTC"
time.tzset()
