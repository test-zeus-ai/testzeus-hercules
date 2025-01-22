from typing import Annotated, Dict, List, Optional, Union

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.geo_handler import GeoLocationSDK
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent", "api_nav_agent"],
    name="get_current_geo_location",
    description=("Retrieve the current geolocation"),
)
async def get_current_geo_location() -> Union[str, Dict[str, str]]:
    """
    Use the browser's current latitude/longitude from PlaywrightManager,
    then call GeoLocationSDK to reverse-geocode into a single best-match address.

    Returns:
    - str: The human-readable address on success.
    - dict with 'error': If something fails or no location is set.
    """
    try:
        # Read provider/api_key from global CONF
        provider = get_global_conf().get_geo_provider()
        api_key = get_global_conf().get_geo_api_key()
        if not provider or not api_key:
            return {"error": "Missing GEO_PROVIDER or GEO_API_KEY in get_global_conf()."}

        # 1) Get lat/long from Playwright
        browser_manager = PlaywrightManager()
        lat_lng = browser_manager.user_geolocation  # e.g., {"latitude": float, "longitude": float}

        if not lat_lng or "latitude" not in lat_lng or "longitude" not in lat_lng:
            return {"error": "No current geolocation set in the browser."}

        # 2) Reverse geocode to get address
        sdk = GeoLocationSDK(provider=provider, api_key=api_key)
        address = sdk.geo_to_address({"lat": lat_lng["latitude"], "lng": lat_lng["longitude"]})
        if not address:
            return {"error": "No matching address found for current geolocation."}
        return address

    except Exception as e:
        logger.exception(f"Error in get_current_geo_location: {e}")
        return {"error": str(e)}


@tool(
    agent_names=["browser_nav_agent", "api_nav_agent"],
    name="set_current_geo_location",
    description="Set the browser geolocation",
)
async def set_current_geo_location(address: Annotated[str, "address"]) -> Union[str, Dict[str, str]]:
    """
    Convert the given address to lat/long via GeoLocationSDK, then set that geolocation
    in PlaywrightManager. The provider/api_key are read from the global CONF object.

    Returns:
    - str: A success message on success.
    - dict with 'error': If something fails or the address can't be geocoded.
    """
    try:
        # Read provider/api_key from global CONF
        provider = get_global_conf().get_geo_provider()
        api_key = get_global_conf().get_geo_api_key()
        if not provider or not api_key:
            return {"error": "Missing GEO_PROVIDER or GEO_API_KEY in get_global_conf()."}

        # 1) Forward geocode the address
        sdk = GeoLocationSDK(provider=provider, api_key=api_key)
        geo = sdk.address_to_geo(address)
        if not geo or "lat" not in geo or "lng" not in geo:
            return {"error": f"Could not find lat/lng for address: '{address}'"}

        latitude = float(geo["lat"])
        longitude = float(geo["lng"])

        # 2) Update Playwright geolocation
        browser_manager = PlaywrightManager()
        await browser_manager.set_geolocation(latitude, longitude)

        success_msg = f"Set browser geolocation to '{address}' " f"(lat={latitude}, lng={longitude})."
        return success_msg

    except Exception as e:
        logger.exception(f"Error in set_current_geo_location: {e}")
        return {"error": str(e)}
