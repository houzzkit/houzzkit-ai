from ..const import DOMAIN
from homeassistant.helpers import entity_registry as er

class Dict(dict):
    def __getattr__(self, item):
        value = self.get(item)
        return Dict(value) if isinstance(value, dict) else value

    def __setattr__(self, key, value):
        self[key] = Dict(value) if isinstance(value, dict) else value


def get_config_entry(hass, speak_id=None, mac=None):
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = Dict(entry.data)
        if speak_id and speak_id == data.speak_id:
            return entry
        if mac and mac == data.mac:
            return entry
    return None

def get_entities(hass, speak_id=None, mac=None):
    entry = get_config_entry(hass, speak_id, mac)
    if not entry:
        return []
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)

def get_entities_ids(hass, speak_id=None, mac=None):
    return [
        entity.entity_id
        for entity in get_entities(hass, speak_id, mac)
    ]
