"""Device Manager to handle creation of appropriate device automation instances."""

from typing import Optional, Union, Dict, Any
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger


class DeviceManager:
    """
    DeviceManager class that provides appropriate device automation instances 
    (Appium for mobile, Playwright for web) based on configuration.
    
    Follows singleton pattern per stake_id like other managers.
    """

    _instances: Dict[str, "DeviceManager"] = {}
    _default_instance: Optional["DeviceManager"] = None

    def __new__(cls, *args: Any, stake_id: Optional[str] = None, **kwargs: Any) -> "DeviceManager":
        # If no stake_id provided and we have a default instance, return it
        if stake_id is None:
            if cls._default_instance is None:
                # Create default instance with stake_id "0"
                instance = super().__new__(cls)
                instance._initialized = False
                cls._default_instance = instance
                cls._instances["0"] = instance
                logger.debug("Created default DeviceManager instance with stake_id '0'")
            return cls._default_instance

        # If stake_id provided, get or create instance for that stake_id
        if stake_id not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instances[stake_id] = instance
            logger.debug(f"Created new DeviceManager instance for stake_id '{stake_id}'")
            # If this is the first instance ever, make it the default
            if cls._default_instance is None:
                cls._default_instance = instance
        return cls._instances[stake_id]

    def __init__(self, stake_id: Optional[str] = None):
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self._initialized = True
        self.stake_id = stake_id or "0"
        self._device_instance = None
        
    @classmethod
    def get_instance(cls, stake_id: Optional[str] = None) -> "DeviceManager":
        """Get DeviceManager instance for given stake_id, or default instance if none provided."""
        if stake_id is None:
            if cls._default_instance is None:
                # This will create the default instance
                return cls()
            return cls._default_instance
        if stake_id not in cls._instances:
            # This will create a new instance for this stake_id
            return cls(stake_id=stake_id)
        return cls._instances[stake_id]

    @classmethod
    def close_instance(cls, stake_id: Optional[str] = None) -> None:
        """Close and remove a specific DeviceManager instance."""
        target_id = stake_id if stake_id is not None else "0"
        if target_id in cls._instances:
            instance = cls._instances[target_id]
            instance.close()  # Close device instance if any
            del cls._instances[target_id]
            if instance == cls._default_instance:
                cls._default_instance = None
                # If there are other instances, make the first one the default
                if cls._instances:
                    cls._default_instance = next(iter(cls._instances.values()))

    @classmethod
    def close_all_instances(cls) -> None:
        """Close all DeviceManager instances."""
        for stake_id in list(cls._instances.keys()):
            cls.close_instance(stake_id)
    
    def close(self) -> None:
        """Close the current device instance if any."""
        if isinstance(self._device_instance, AppiumManager):
            AppiumManager.close_instance(self.stake_id)
        elif isinstance(self._device_instance, PlaywrightManager):
            PlaywrightManager.close_instance(self.stake_id)
        self._device_instance = None

    def get_device_instance(self) -> Union[AppiumManager, PlaywrightManager]:
        """
        Get the appropriate device automation instance based on configuration.
        
        Returns:
            Union[AppiumManager, PlaywrightManager]: The appropriate device manager instance
            based on device_manager and device_os config values.
        
        The device_manager config can be:
        - "appium" for mobile automation
        - "playwright" for web automation (default)
        
        For Appium, device_os should be:
        - "android" (default) 
        - "ios"
        """
        if self._device_instance is not None:
            return self._device_instance
        
        conf = get_global_conf()
        device_manager = conf.get_device_manager()
        
        if device_manager == "appium":
            device_os = conf.get_device_os()
            logger.info(f"Initializing Appium manager for {device_os} device")
            
            if device_os == "ios":
                self._device_instance = AppiumManager(
                    stake_id=self.stake_id,
                    platformName="iOS",
                    automationName="XCUITest"
                )
            else:  # android
                self._device_instance = AppiumManager(
                    stake_id=self.stake_id,
                    platformName="Android",
                    automationName="UiAutomator2"
                )
        else:  # playwright
            self._device_instance = PlaywrightManager(stake_id=self.stake_id)
            
        return self._device_instance