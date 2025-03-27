# OneBusAway Home Assistant Integration

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

Select the routes you'd like to monitor, or continue to monitor all routes from that stop.

## Recommended Configurations
Add an exclusion filter in your recorder for the created sensors, to maintain database size.

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.onebusaway_*
```

Use a heading card to display stop name and next update

```yaml
type: heading
heading: [Add Stop Name]
heading_style: title
badges:
  - type: entity
    show_state: true
    show_icon: true
    entity: sensor.onebusaway_[stop_id_number]_next_refresh
```

Use markdown card to display ongoing situations.

```yaml
type: markdown
content: |
  {{ states.sensor.onebusaway_[stop_id_number]_situations.attributes.markdown_content }}
visibility:
  - condition: numeric_state
    entity: sensor.onebusaway_[stop_id_number]_situations
    above: 0
text_only: true
```

Use a tile card for no arrival state

```yaml
type: tile
entity: sensor.onebusaway_[stop_id_number]_arrival_0
features_position: bottom
vertical: false
hide_state: true
name: No Upcoming Arrivals
grid_options:
  columns: full
  rows: 1
visibility:
  - condition: or
    conditions:
      - condition: state
        entity: sensor.onebusaway_[stop_id_number]_arrival_0
        state: unavailable
      - condition: state
        entity: sensor.onebusaway_[stop_id_number]_arrival_0
        state: unknown
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
    - entity_id: sensor.onebusaway_[stop_id_number]_next_refresh
    - entity_id: sensor.onebusaway_[stop_id_number]_situations
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
