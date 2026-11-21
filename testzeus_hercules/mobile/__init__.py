"""Native app testing: a case's steps run on an Android or iOS device through Appium (local or a device cloud).

* :mod:`~testzeus_hercules.mobile.target`: where the device is (local Appium server, BrowserStack, Sauce Labs),
  its credentials and the session's capabilities, checked against the egress policy;
* :mod:`~testzeus_hercules.mobile.webdriver`: the W3C WebDriver session over plain HTTP;
* :mod:`~testzeus_hercules.mobile.screen`: the page source as a numbered element list;
* :mod:`~testzeus_hercules.mobile.session`: the per-step loop (read, decide, act, verify);
* :mod:`~testzeus_hercules.mobile.cloud`: device cloud app upload and session status.

Mobile web emulation (a browser with a phone's viewport and user agent) is not here: it is a browser run with
``environment.device`` (see :mod:`testzeus_hercules.cases.devices`).
"""
