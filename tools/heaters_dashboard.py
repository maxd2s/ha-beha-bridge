#!/usr/bin/env python3
"""Generate a LAN Heaters dashboard from the bridge's heater configuration."""
import argparse
import json
from pathlib import Path
import re


def dashboard(options):
    sections = [{"type": "grid", "cards": [
        {"type": "heading", "heading": "LAN bridge", "icon": "mdi:lan-connect"},
        {"type": "entities", "entities": ["binary_sensor.beha_lan_bridge_health"]},
        {"type": "markdown", "content": "Local feedback from each heater. Dial changes update the target automatically. A heater becomes unavailable when its readings stop."}]}]
    for heater in options['heaters']:
        hid = heater['device_id'].lower().replace(':', '').replace('-', '')
        if not re.fullmatch('[0-9a-f]{12}', hid):
            raise ValueError('Invalid device ID')
        sections.append({"type": "grid", "cards": [{"type": "vertical-stack", "cards": [
            {"type": "thermostat", "entity": f"climate.beha_lan_{hid}",
             "name": heater.get('name') or f'BEHA {hid[-6:]}', "show_current_as_primary": True,
             "features": [{"type": "climate-hvac-modes", "hvac_modes": ["off", "heat"]}]},
            {"type": "entities", "show_header_toggle": False, "entities": [
                {"entity": f"switch.beha_lan_{hid}_child_lock", "name": "Child lock"},
                {"entity": f"select.beha_lan_{hid}_heater_mode", "name": "Control mode"}]}]}]})
    return {"title": "Heaters", "views": [{"title": "Heaters", "path": "overview",
             "type": "sections", "max_columns": 3, "sections": sections}]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--options', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(dashboard(json.loads(Path(args.options).read_text())), indent=2) + '\n')
