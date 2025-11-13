import logging
from dataclasses import dataclass
from typing import Any, Literal

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

_LOGGER = logging.getLogger(__name__)

def get_entity_name(entity_entry: er.RegistryEntry) -> str:
    if len(entity_entry.aliases) > 0:
        return list(entity_entry.aliases)[0]
    if entity_entry.name:
        return entity_entry.name
    
    if entity_entry.name:
        return entity_entry.name
    return ""

@dataclass
class AreaInfo:
    name: str
    id: str

def get_entity_area(hass: HomeAssistant, entity_entry: er.RegistryEntry) -> AreaInfo | None:
    area_names = []
    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    if entity_entry.area_id and (
        area := area_registry.async_get_area(entity_entry.area_id)
    ):
        # Entity is in area
        area_names.extend(area.aliases)
        area_names.append(area.name)
        if len(area_names) == 0:
            return
        return AreaInfo(id=entity_entry.area_id, name=area_names[0])
    elif entity_entry.device_id and (
        device := device_registry.async_get(entity_entry.device_id)
    ):
        # Check device area
        if device.area_id and (
            area := area_registry.async_get_area(device.area_id)
        ):
            area_names.extend(area.aliases)
            area_names.append(area.name)
            if len(area_names) == 0:
                return
            return AreaInfo(id=device.area_id, name=area_names[0])

@dataclass
class EntityInfo:
    name: str
    area: AreaInfo | None
    state: State
    entity: er.RegistryEntry
    on_off: Literal["on", "off"]
    
    @property
    def area_name(self) -> str:
        if self.area:
            return self.area.name
        return ""
    
    @property
    def area_id(self) -> str:
        if self.area:
            return self.area.id
        return ""

async def match_intent_entities(intent_obj: intent.Intent, slots: dict[str, Any]) -> tuple[dict | None, list[EntityInfo] | None]:
    """Match entities by request parameters."""
    domain: str = slots.get("domain", {}).get("value")
    name: str | None  = slots.get("name", {}).get("value")
    area_name: str | None = slots.get("area", {}).get("value")
    floor_name: str | None = slots.get("floor", {}).get("value")
    except_area: list[str] | None = slots.get("except_area", {}).get("value")
    preferred_area_id: str | None = slots.get("preferred_area_id", {}).get("value")
    
    _LOGGER.info(
        f"Match intent params: slots={slots} "
    )
    
    # In the exclude case, names must be specified.
    if except_area:
        if name is None:
            name = "all"
        if area_name is None:
            area_name = "all"
    
    # Fix argument issues in special cases.
    if name == "all" and area_name is None:
        area_name = "all"
    if area_name == "all" and name is None:
        name = "all"
    
    # name: $name/"all"/None
    filter_name = None if name == "all" else name
    filter_area_name = None if area_name == "all" else area_name
    
    hass = intent_obj.hass
    match_constraints = intent.MatchTargetsConstraints(
        name=filter_name,
        area_name=filter_area_name,
        floor_name=floor_name,
        domains={domain},
        assistant=intent_obj.assistant,
        single_target=False,
    )
    
    match_result = intent.async_match_targets(
        hass, match_constraints
    )
    if not match_result.is_match:
        raise intent.MatchFailedError(
            result=match_result, constraints=match_constraints
        )
    assert match_result.states
    
    # Filter out candidate targets.
    candidate_entities: list[EntityInfo] = []
    for state in match_result.states:
        if state.state == "unavailable":
            continue
        
        entity_registry = er.async_get(hass)
        entity_entry = entity_registry.async_get(state.entity_id)
        if not entity_entry:
            continue
        
        entity_name = get_entity_name(entity_entry)
        entity_area = get_entity_area(hass, entity_entry)
        on_off = "off" if state.state == "off" else "on"
        entity_info = EntityInfo(name=entity_name, area=entity_area, state=state, entity=entity_entry, on_off=on_off)
        _LOGGER.info(f"Match intent available target: {entity_info}")
        candidate_entities.append(entity_info)
        
    # Remove entities in the excluded areas.
    if except_area:
        for item in candidate_entities.copy():
            if item.area_name in except_area:
                candidate_entities.remove(item)
                
    # No any available.
    if len(candidate_entities) == 0:
        return {
            "success": False,
            "error": "No available devices found"
        }, None
    
    # Filter preferred.
    preferred_candidate_entities = []
    if area_name is None and preferred_area_id:
        for item in candidate_entities:
            if item.area_id == preferred_area_id:
                preferred_candidate_entities.append(item)
                _LOGGER.info(f"Match intent preferred target: area_name={area_name} entity={item}")
                
        if len(preferred_candidate_entities) > 0:
            candidate_entities = preferred_candidate_entities
    
    # If multiple candidates and name is unspecified, let user to choose.
    if name is None and len(candidate_entities) > 1:
        candidate_targets = []
        entity_key_map = set() # for deduplication
        for item in candidate_entities:
            entity_key = f"{item.area_name}-{item.name}"
            if entity_key not in entity_key_map:
                candidate_targets.append({"name": item.name, "area": item.area_name})
                entity_key_map.add(entity_key)
        return {
            "success": False,
            "error": "Need to select one",
            "candidate_targets": candidate_targets
        }, None
        
    return None, candidate_entities
