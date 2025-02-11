# OneBusAway Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

Integration to bring data from [OneBusAway](https://onebusaway.org/)
into Home Assistant.  

This is only tested with [Puget Sound OneBusAway](https://pugetsound.onebusaway.org/). 

## Install
Use HACS and add as a custom repository. Once the custom integration is installed, restart HA and add the OneBusAway integration. It will prompt you for the following configuration parameters:

| Parameter | Notes |
| :--- | :--- |
| URL | no change needed for use with Puget Sound OneBusAway | 
| Token | request an API key from [Sound Transit](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd) |
| ID | Use [Puget Sound OneBusAway](https://pugetsound.onebusaway.org/) map to zoom in and identify the stop you are interested in, select it, and view schedule. The id will be appended at the end of the url. |

There must be routes scheduled to arrive during setup, otherwise it will not complete.

## Recommended Configurations
Add an exclusion filter in your recorder for the created sensors, to maintain database size.

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.onebusaway_*
```
Use markdown card to display ongoing situations.

```yaml
type: markdown
content: |
  ## Situations
  {{ states.sensor.onebusaway_[stop_id_number]_situations.attributes.markdown_content }}
visibility:
  - condition: numeric_state
    entity: sensor.onebusaway_[stop_id_number]_situations
    above: 0
```
Use custom [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) card to create a view of all incoming arrivals for a specific stop(s).

```yaml
type: custom:auto-entities
card:
  type: entities
filter:
  include:
    - entity_id: sensor.onebusaway_[stop_id_number]*
      # add the following to filter only selected routes from the chosen stop
      attributes:
        route_name: '[selected_route_name]'
  exclude:
    - state: unknown
    - state: unavailable
sort:
  method: state
visibility:
  - condition: state
    entity: sensor.onebusaway_[stop_id_number]_arrival_0
    state_not: unavailable
  - condition: state
    entity: sensor.onebusaway_[stop_id_number]_arrival_0
    state_not: unknown

```
