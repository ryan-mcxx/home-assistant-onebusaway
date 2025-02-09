# OneBusAway Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

Integration to bring data from [OneBusAway](https://onebusaway.org/)
into Home Assistant.

## Supported

This is only tested with [Puget Sound OneBusAway](https://pugetsound.onebusaway.org/). 

## Install
Use HACS and add as a custom repo. Once the integration is installed go to your integrations and add the OneBusAway integration. It will prompt you for some configuration parameters:

<b>URL:</b> don't change  
<b>Token:</b> request an API key from [Sound Transit](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd)  
<b>ID:</b> Use [Puget Sound OneBusAway](https://pugetsound.onebusaway.org/) map to zoom in and identify the stop you are interested in, select it, and view schedule. The id will be at the end of the url.  

There must be routes scheduled to arrive during setup, otherwise it will not complete.

## Home Assistant Frontend
I recommend using [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) to create a view of all incoming arrivals for a specific stop(s).

```yaml
type: custom:auto-entities
card:
  type: entities
filter:
  include:
    - entity_id: sensor.onebusaway_(stop_id_number)*
  exclude:
    - state: unknown
    - state: unavailable
sort:
  method: state
```
