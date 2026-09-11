from collections.abc import Callable

from testzeus_hercules.utils.logger import logger


class NotificationManager:
    """
    NotificationManager handles the dispatching of notifications to registered listeners.

    Attributes:
        listeners (list[Callable[[dict[str, str]], None]]): A list of listener callbacks to notify.
    """

    def __init__(self):
        """
        Initialize the NotificationManager with no listeners.
        """
        self.listeners: list[Callable[[dict[str, str]], None]] = []

    def notify(self, message: str, message_type: str) -> None:
        """
        Notify all registered listeners with a message and its type.

        Every notification dispatched on a non-hub instance is also forwarded
        to the module-level ``notification_hub`` so process-wide listeners
        (e.g. the MCP runner bridge) observe events from any manager instance.
        Forwarding never raises and cannot recurse (the hub itself does not
        forward).

        Args:
            message (str): The message to notify.
            message_type (str): The type of the message.
        """
        notification = {
            "message": message,
            "type": message_type,
        }

        for listener in list(self.listeners):
            try:
                listener(notification)
            except Exception as exc:
                # One raising listener must never break delivery to the others.
                logger.error(f"Notification listener {getattr(listener, '__qualname__', listener)} failed: {exc}")

        if self is not notification_hub:
            try:
                notification_hub.notify(message, message_type)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(f"Failed to forward notification to hub: {exc}")

        if not self.listeners:
            logger.debug(f"No listeners available, discarding message: {notification}")

    def register_listener(self, listener: Callable[[dict[str, str]], None]) -> None:
        """
        Register a new listener to receive notifications.

        Args:
            listener (Callable[[dict[str, str]], None]): The listener callback to register.
        """
        self.listeners.append(listener)

    def unregister_listener(self, listener: Callable[[dict[str, str]], None]) -> None:
        """
        Unregister a listener from receiving notifications.

        Unknown (not registered) listeners are ignored and logged at debug
        instead of raising.

        Args:
            listener (Callable[[dict[str, str]], None]): The listener callback to unregister.
        """
        try:
            self.listeners.remove(listener)
        except ValueError:
            logger.debug(f"unregister_listener called for a listener that is not registered: {listener}")


# Module-level process-wide notification hub. Every NotificationManager
# instance forwards its notifications here (see NotificationManager.notify),
# so listeners registered on the hub observe events from any instance.
notification_hub = NotificationManager()


def get_notification_hub() -> NotificationManager:
    """
    Return the process-wide notification hub singleton.

    Listeners registered on this hub receive every notification dispatched
    through any NotificationManager instance in the process.
    """
    return notification_hub
