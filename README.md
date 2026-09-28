# Samil Power inverter – Home Assistant custom integration

Local polling of Samil Power **SolarRiver TL-D** and **SolarLake TL** inverters
over the network. Nothing goes through the cloud. Based on the protocol work in
[mhvis/solar](https://github.com/mhvis/solar) (MIT).

## How it talks to the inverter (read this if you use Docker)

Home Assistant **listens on TCP port 1200** and sends an `I AM SERVER` message
to **UDP port 1300**. The inverter then **opens a TCP connection back** to Home
Assistant on port 1200.

* **Docker with `network_mode: host`** (the standard HA Docker setup): works
  out of the box. Leave both setup fields empty.
* **Docker on a bridge network**: broadcasts won't reach the LAN. Publish the
  port (`-p 1200:1200` / `ports: ["1200:1200"]`) and enter the inverter's IP
  address during setup, so the discovery message is sent to it directly. This
  mode is untested on real hardware. Host networking is the reliable option.
* The inverter only talks to **one program at a time**, and only one program
  per host can listen on port 1200. **Stop the `mhvis/samil` container**
  (`samil mqtt`, SolarPower Browser, etc.) before setting this up, wherever it
  runs on your network.
* Logs that say something like `Connected to inverter ... ('192.168.1.221', 4100)`
  show the inverter's *outgoing* source port. You never need to configure it:
  the inverter always connects to port 1200 on the Home Assistant host.

## Install

1. Copy `custom_components/samil` into your HA config folder, so you end up
   with `<config>/custom_components/samil/manifest.json`.
   (Or with HACS: *HACS → ⋮ → Custom repositories*, add `https://github.com/danfulton72/samil_ha`, type *Integration*.)
2. Restart Home Assistant.
3. **During daylight** (the inverter switches off its network at night), go to
   *Settings → Devices & services → Add integration → Samil Power Inverter*.
   Discovery takes up to ~20 seconds.

## Entities

| Sensor | Unit | When the inverter is off at night |
|---|---|---|
| Operation mode | Wait / Normal / Fault / … | `Offline` |
| Output power, PV1/PV2 input power | W | 0 |
| PV1/PV2 current, grid current | A | 0 |
| Energy today, Energy total | kWh | last value kept |
| PV1/PV2 voltage, grid voltage, grid frequency | V, Hz | unavailable |
| Internal / heatsink temperature | °C | unavailable |
| Total operation time (diagnostic) | h | last value kept |

Three-phase models get R/S/T grid voltage, current and frequency instead of the
single grid sensors. Sensors the inverter doesn't report show as unavailable,
and you can disable them.

**Energy dashboard:** add *Energy total* under *Solar production*.

Polling interval defaults to 10 s (*Configure* on the integration, 5–300 s).
While the inverter is offline, the integration looks for it once a minute.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.samil: debug
```

## License

MIT. Protocol code adapted from mhvis/solar, © 2016 Maarten Visscher.

## Development

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest   # runs HA end-to-end against a simulated inverter on localhost
```
