#!/usr/bin/env python3
"""Generate a LAN Heaters dashboard from the bridge's heater configuration."""
import argparse
import json
from pathlib import Path
import re


def dashboard(options):
    sections = []
    for heater in options['heaters']:
        hid = heater['device_id'].lower().replace(':', '').replace('-', '')
        if not re.fullmatch('[0-9a-f]{12}', hid):
            raise ValueError('Invalid device ID')
        sections.append({"type": "grid", "cards": [
            {"type": "custom:beha-thermostat-card", "entity": f"climate.beha_lan_{hid}",
             "name": heater.get('name') or f'BEHA {hid[-6:]}',
             "child_lock_entity": f"switch.beha_lan_{hid}_child_lock",
             "grid_options": {"columns": 12, "rows": 6}}]})
    return {"title": "Heaters", "views": [{"title": "Heaters", "path": "overview",
             "type": "sections", "max_columns": 3, "sections": sections}]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--options', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(dashboard(json.loads(Path(args.options).read_text())), indent=2) + '\n')
