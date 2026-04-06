from enum import Enum
from threading import Lock
from typing import Any, Sequence

from icloudpd.log_buffer import LogBuffer
from icloudpd.progress import Progress


class Status(Enum):
    NO_INPUT_NEEDED = "no_input_needed"
    NEED_MFA = "need_mfa"
    SUPPLIED_MFA = "supplied_mfa"
    CHECKING_MFA = "checking_mfa"
    NEED_PASSWORD = "need_password"
    SUPPLIED_PASSWORD = "supplied_password"
    CHECKING_PASSWORD = "checking_password"

    def __str__(self) -> str:
        return self.name


class TrustedDeviceInfo:
    """Serializable trusted device info for web UI display."""

    def __init__(self, device_id: int, obfuscated_number: str) -> None:
        self.device_id = device_id
        self.obfuscated_number = obfuscated_number


class StatusExchange:
    def __init__(self) -> None:
        self.lock = Lock()
        self._status = Status.NO_INPUT_NEEDED
        self._payload: str | None = None
        self._error: str | None = None
        self._global_config: Any | None = None
        self._user_configs: Sequence[Any] = []
        self._current_user: str | None = None
        self._progress = Progress()
        self._log_buffer = LogBuffer()
        self._trusted_devices: Sequence[TrustedDeviceInfo] = []
        self._sms_request_device_id: int | None = None
        self._sms_sent_device_id: int | None = None

    def get_status(self) -> Status:
        with self.lock:
            return self._status

    def replace_status(self, expected_status: Status, new_status: Status) -> bool:
        with self.lock:
            if self._status == expected_status:
                self._status = new_status
                return True
            else:
                return False

    def set_payload(self, payload: str) -> bool:
        with self.lock:
            if self._status != Status.NEED_MFA and self._status != Status.NEED_PASSWORD:
                return False

            self._payload = payload
            self._status = (
                Status.SUPPLIED_MFA if self._status == Status.NEED_MFA else Status.SUPPLIED_PASSWORD
            )
            self._error = None
            return True

    def get_payload(self) -> str | None:
        with self.lock:
            if self._status not in [
                Status.SUPPLIED_MFA,
                Status.CHECKING_MFA,
                Status.SUPPLIED_PASSWORD,
                Status.CHECKING_PASSWORD,
            ]:
                return None

            return self._payload

    def set_error(self, error: str) -> bool:
        with self.lock:
            if self._status != Status.CHECKING_MFA and self._status != Status.CHECKING_PASSWORD:
                return False

            self._error = error
            self._status = (
                Status.NO_INPUT_NEEDED
                if self._status == Status.CHECKING_PASSWORD
                else Status.NEED_MFA
            )
            return True

    def get_error(self) -> str | None:
        with self.lock:
            if self._status not in [
                Status.NO_INPUT_NEEDED,
                Status.NEED_PASSWORD,
                Status.NEED_MFA,
            ]:
                return None

            return self._error

    def get_progress(self) -> Progress:
        with self.lock:
            return self._progress

    def get_log_buffer(self) -> LogBuffer:
        with self.lock:
            return self._log_buffer

    def set_global_config(self, global_config: Any) -> None:
        with self.lock:
            self._global_config = global_config

    def get_global_config(self) -> Any | None:
        with self.lock:
            return self._global_config

    def set_user_configs(self, user_configs: Sequence[Any]) -> None:
        with self.lock:
            self._user_configs = user_configs

    def get_user_configs(self) -> Sequence[Any]:
        with self.lock:
            return self._user_configs

    def set_current_user(self, username: str) -> None:
        with self.lock:
            self._current_user = username

    def get_current_user(self) -> str | None:
        with self.lock:
            return self._current_user

    def clear_current_user(self) -> None:
        with self.lock:
            self._current_user = None

    def set_trusted_devices(self, devices: Sequence[TrustedDeviceInfo]) -> None:
        with self.lock:
            self._trusted_devices = devices

    def get_trusted_devices(self) -> Sequence[TrustedDeviceInfo]:
        with self.lock:
            return self._trusted_devices

    def request_sms(self, device_id: int) -> bool:
        """Web UI calls this to request SMS be sent to a device."""
        with self.lock:
            if self._status != Status.NEED_MFA:
                return False
            self._sms_request_device_id = device_id
            return True

    def consume_sms_request(self) -> int | None:
        """Auth loop calls this to pick up and clear a pending SMS request."""
        with self.lock:
            device_id = self._sms_request_device_id
            self._sms_request_device_id = None
            return device_id

    def set_sms_sent(self, device_id: int) -> None:
        with self.lock:
            self._sms_sent_device_id = device_id

    def get_sms_sent_device_id(self) -> int | None:
        with self.lock:
            return self._sms_sent_device_id

    def clear_mfa_state(self) -> None:
        """Reset MFA-related state between auth attempts."""
        with self.lock:
            self._trusted_devices = []
            self._sms_request_device_id = None
            self._sms_sent_device_id = None
