import httpx


class GeoLocationSDK:
    """
    A class to interact with the Google Maps and Maps.co APIs.
    """

    def __init__(self, provider: str, api_key: str) -> None:
        """
        Initialize the SDK with the provider and its corresponding API key.

        Parameters:
        - provider (str): Either 'google' or 'maps_co'
        - api_key (str): The API key for the chosen provider
        """
        if provider not in ["google", "maps_co"]:
            raise ValueError("Provider must be either 'google' or 'maps_co'.")

        self.provider = provider
        self.api_key = api_key
        self.maps_co_base_url = "https://geocode.maps.co"

    def _validate_provider(self, expected_provider: str) -> None:
        """
        Validate that the current provider matches the expected provider.
        """
        if self.provider != expected_provider:
            raise ValueError(f"This method requires the '{expected_provider}' provider.")

    # ----------------------------------------------------------------
    # 1) Convert (lat, lng) -> a single address
    # ----------------------------------------------------------------
    def geo_to_address(self, geo_location: dict, hint: str | None = None) -> str:
        """
        Convert geo_location to address.

        Parameters:
        - geo_location (dict): A dictionary with 'lat' and 'lng' keys
        - hint (str): Optional hint to match a specific address

        Returns:
        - str: The matched address (or None if not found)
        """
        if self.provider == "google":
            self._validate_provider("google")
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "latlng": f"{geo_location['lat']},{geo_location['lng']}",
                "key": self.api_key,
            }
        elif self.provider == "maps_co":
            self._validate_provider("maps_co")
            url = f"{self.maps_co_base_url}/reverse"
            params = {
                "lat": geo_location["lat"],
                "lon": geo_location["lng"],
                "api_key": self.api_key,
            }
        else:
            raise ValueError("Invalid provider configured.")

        response = httpx.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if self.provider == "google":
            # Google response is typically { "status": "OK"|"ERROR", "results": [...] }
            if data.get("status") != "OK":
                raise Exception(f"Google Maps API error: {data.get('error_message')}")
            results = data.get("results", [])
            # Format for single best match
            addresses = [r.get("formatted_address") for r in results if r.get("formatted_address")]
        else:
            # maps_co reverse returns a single dict
            # Example:
            # {
            #   "place_id": ...,
            #   "display_name": "Phoenix Market City, West End Road...",
            #   "lat": "...", "lon": "...",
            #   "address": { ... },
            #   "boundingbox": [ ... ]
            # }
            if isinstance(data, dict):
                addresses = [data.get("display_name")]
            elif isinstance(data, list):
                # theoretically can happen, but typically reverse returns just one object
                addresses = [x.get("display_name") for x in data]
            else:
                addresses = []

        # Filter out None or empty
        addresses = [a for a in addresses if a]

        # If hint is provided, try to match it
        if hint and addresses:
            hint_lower = hint.lower()
            for addr in addresses:
                if hint_lower in addr.lower():
                    return addr

        # Otherwise return the first address if available
        return addresses[0] if addresses else None

    # ----------------------------------------------------------------
    # 2) Convert address -> a single geo_location (lat/lng)
    # ----------------------------------------------------------------
    def address_to_geo(self, address: str, hint: str | None = None) -> dict:
        """
        Convert address to geo_location.

        Parameters:
        - address (str): The address string to geocode
        - hint (str): Optional hint to match a specific geo-location

        Returns:
        - dict: A dictionary with 'lat' and 'lng' keys
        """
        if self.provider == "google":
            self._validate_provider("google")
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": address,
                "key": self.api_key,
            }
        elif self.provider == "maps_co":
            self._validate_provider("maps_co")
            url = f"{self.maps_co_base_url}/search"
            params = {
                "q": address,
                "api_key": self.api_key,
            }
        else:
            raise ValueError("Invalid provider configured.")

        response = httpx.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if self.provider == "google":
            # e.g. { "status": "OK", "results": [ { "formatted_address": ..., "geometry":{ ...} }, ... ] }
            if data.get("status") != "OK":
                raise Exception(f"Google Maps API error: {data.get('error_message')}")
            results = data.get("results", [])
        else:
            # maps_co can return a list for search or a single dict
            if isinstance(data, dict):
                # Rare case if the API returned a single object
                results = [data]
            elif isinstance(data, list):
                results = data
            else:
                results = []

        # If 'hint' is provided, try to find a match in the results
        if hint:
            hint_lower = hint.lower()
            for r in results:
                address_match = r.get("formatted_address") if self.provider == "google" else r.get("display_name")
                if address_match and hint_lower in address_match.lower():
                    if self.provider == "google":
                        return r.get("geometry", {}).get("location", {})
                    else:
                        # For maps_co
                        return {
                            "lat": r.get("lat"),
                            "lng": r.get("lon"),
                        }

        # No hint or hint not matched -> take first
        if results:
            if self.provider == "google":
                return results[0].get("geometry", {}).get("location", {})
            else:
                return {
                    "lat": results[0].get("lat"),
                    "lng": results[0].get("lon"),
                }

        return {}

    # ----------------------------------------------------------------
    # 3) Convert (lat, lng) -> a list of all possible addresses
    # ----------------------------------------------------------------
    def geo_to_address_all(self, geo_location: dict) -> list:
        """
        Return all addresses for the given geo_location.

        Parameters:
        - geo_location (dict): A dictionary with 'lat' and 'lng' keys

        Returns:
        - list of str: A list of matched address strings
        """
        if self.provider == "google":
            self._validate_provider("google")
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "latlng": f"{geo_location['lat']},{geo_location['lng']}",
                "key": self.api_key,
            }
        elif self.provider == "maps_co":
            self._validate_provider("maps_co")
            url = f"{self.maps_co_base_url}/reverse"
            params = {
                "lat": geo_location["lat"],
                "lon": geo_location["lng"],
                "api_key": self.api_key,
            }
        else:
            raise ValueError("Invalid provider configured.")

        response = httpx.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if self.provider == "google":
            if data.get("status") != "OK":
                raise Exception(f"Google Maps API error: {data.get('error_message')}")
            return [r.get("formatted_address") for r in data.get("results", []) if r.get("formatted_address")]
        else:
            # maps_co reverse -> single dict or list
            if isinstance(data, dict):
                # Single
                return [data.get("display_name")] if data.get("display_name") else []
            elif isinstance(data, list):
                # List
                return [x.get("display_name") for x in data if x.get("display_name")]
            return []

    # ----------------------------------------------------------------
    # 4) Convert address -> a list of all possible geo_locations
    # ----------------------------------------------------------------
    def address_to_geo_all(self, address: str) -> list:
        """
        Return all geo_locations (lat/lng) for the given address.

        Parameters:
        - address (str): The address string to geocode

        Returns:
        - list of dict: A list of geo-location dictionaries, each with 'lat' and 'lng' keys
        """
        if self.provider == "google":
            self._validate_provider("google")
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": address,
                "key": self.api_key,
            }
        elif self.provider == "maps_co":
            self._validate_provider("maps_co")
            url = f"{self.maps_co_base_url}/search"
            params = {
                "q": address,
                "api_key": self.api_key,
            }
        else:
            raise ValueError("Invalid provider configured.")

        response = httpx.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if self.provider == "google":
            if data.get("status") != "OK":
                raise Exception(f"Google Maps API error: {data.get('error_message')}")
            # Each result has geometry.location
            return [r.get("geometry", {}).get("location") for r in data.get("results", []) if r.get("geometry", {}).get("location") is not None]
        else:
            # maps_co -> can be a list or a single dict
            if isinstance(data, dict):
                data_list = [data]
            elif isinstance(data, list):
                data_list = data
            else:
                data_list = []

            results = []
            for item in data_list:
                lat = item.get("lat")
                lon = item.get("lon")
                if lat and lon:
                    results.append({"lat": lat, "lng": lon})
            return results
