"""
kaoruko/execution/handlers/notification.py
Windows toast notifications.
"""
from __future__ import annotations
from kaoruko.infrastructure.logging.logger import get_logger
log = get_logger("execution.notification")

try:
    from win10toast import ToastNotifier
    _TOAST = True
except ImportError:
    _TOAST = False

class NotificationHandler:
    def __init__(self, config) -> None:
        self.config = config
        self._toaster = ToastNotifier() if _TOAST else None

    def show_notification(
        self,
        title: str = "Kaoruko",
        message: str = "",
        duration: int = 5,
        **kwargs,
    ) -> str:
        if self._toaster:
            try:
                self._toaster.show_toast(title, message, duration=duration, threaded=True)
            except Exception as e:
                log.error("toast_error", error=str(e))
        else:
            log.info("notification", title=title, message=message)
        return ""
