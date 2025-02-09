# OneBusAway Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

Integration to bring data from [OneBusAway](https://onebusaway.org/)
into Home Assistant.

## Install
Use HACS and add as a custom repo. Once the integration is installed go to your integrations and add the OneBusAway integration. It will prompt you for some configuration parameters:

url: don't change
token: request an API key from [Sound Transit](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd)
id: Use [Puget Sound OneBusAway](https://pugetsound.onebusaway.org/) map to zoom in and identify the stop you are interested in and select it, and view schedule. The id will be in the url of that link.

There must be routes scheduled to arrive during setup, otherwise it will not complete.
-

## Supported

This is only tested with [Puget Sound OneBusAway](https://pugetsound.onebusaway.org/). Let me know
if you have successfully used it with other transit agencies!

