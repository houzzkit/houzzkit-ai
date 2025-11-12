import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypedDict
import voluptuous as vol
import logging
from operator import attrgetter
from homeassistant.helpers import entity_registry as er, intent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.llm import CALENDAR_DOMAIN, SCRIPT_DOMAIN
from homeassistant.util.json import JsonObjectType
from decimal import Decimal
from homeassistant.util import dt as dt_util, yaml as yaml_util
from homeassistant.components.homeassistant import async_should_expose

from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from homeassistant.components import http, sensor
from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    SERVICE_PRESS as SERVICE_PRESS_BUTTON,
    ButtonDeviceClass,
)
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.cover import (
    ATTR_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    CoverDeviceClass,
)
from homeassistant.components.http.data_validator import RequestDataValidator
from homeassistant.components.input_button import DOMAIN as INPUT_BUTTON_DOMAIN
from homeassistant.components.lock import (
    DOMAIN as LOCK_DOMAIN,
    SERVICE_LOCK,
    SERVICE_UNLOCK,
)
from homeassistant.components.media_player import MediaPlayerDeviceClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
    SERVICE_SET_VALVE_POSITION,
    ValveDeviceClass,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    integration_platform,
    intent,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from homeassistant.components.intent.const import DOMAIN, TIMER_DATA
from homeassistant.components.intent.timers import (
    CancelAllTimersIntentHandler,
    CancelTimerIntentHandler,
    DecreaseTimerIntentHandler,
    IncreaseTimerIntentHandler,
    PauseTimerIntentHandler,
    StartTimerIntentHandler,
    TimerEventType,
    TimerInfo,
    TimerManager,
    TimerStatusIntentHandler,
    UnpauseTimerIntentHandler,
    async_device_supports_timers,
    async_register_timer_handler,
)


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

class TurnDeviceIntentBase(intent.IntentHandler):
    service_timeout = 3

    async def _async_handle(self, intent_obj: intent.Intent, service: Literal["turn_on", "turn_off"]) -> JsonObjectType:
        """Get the current state of exposed entities."""
        slots = self.async_validate_slots(intent_obj.slots)
        domain: str = slots.get("domain", {}).get("value")
        name: str | None  = slots.get("name", {}).get("value")
        area_name: str | None = slots.get("area", {}).get("value")
        floor_name: str | None = slots.get("floor", {}).get("value")
        except_area: list[str] | None = slots.get("except_area", {}).get("value")
        preferred_area_id: str | None = slots.get("preferred_area_id", {}).get("value")
        
        _LOGGER.info(
            f"TurnDeviceOn params: slots={slots} "
        )
        
        UNSPECIFIED = "unspecified"
        filter_name = None if name == UNSPECIFIED else name
        filter_area_name = None if area_name == UNSPECIFIED else area_name
        
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
            _LOGGER.info(f"TurnDeviceOn match target: {state}")
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
            _LOGGER.info(f"TurnDeviceOn available target:{entity_info}")
            candidate_entities.append(entity_info)
            
        if except_area:
            # Remove entities in the excluded areas.
            for item in candidate_entities.copy():
                if item.area_name in except_area:
                    candidate_entities.remove(item)
                if preferred_area_id and item.area_id == preferred_area_id:
                    preferred_area_id = None
                    
        if len(candidate_entities) == 0:
            return {
                "success": False,
                "error": "No available devices found"
            }
        
        # If multiple candidates and name is unspecified, let user to choose.
        if not except_area and name == UNSPECIFIED:
            preferred_candidate_entities = []
            if preferred_area_id:
                for item in candidate_entities:
                    if item.area_id == preferred_area_id:
                        preferred_candidate_entities.append(item)
            if not preferred_candidate_entities:
                # No preferred, need choose.
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
                }
            else:
                # Operate entities in prefered area.
                candidate_entities = preferred_candidate_entities
        
        # Execute operation.
        control_targets = []
        entity_key_map = set() # for deduplication
        for item in candidate_entities:
            _LOGGER.info(f"TurnDeviceOn operate target:{entity_info}")
            await self.handle_match_target(intent_obj, item.state, service)
            
            entity_key = f"{item.area_name}-{item.name}"
            if entity_key not in entity_key_map:
                entity_key_map.add(entity_key)
                control_targets.append({"name": item.name, "area": item.area_name})

        return {
            "success": True,
            "control_targets": control_targets,
        }

    async def handle_match_target(self, intent_obj: intent.Intent, state: State, service: str):
        hass = intent_obj.hass
        if state.domain in (BUTTON_DOMAIN, INPUT_BUTTON_DOMAIN):
            if service != SERVICE_TURN_ON:
                raise intent.IntentHandleError(
                    f"Entity {state.entity_id} cannot be turned off"
                )

            await self._run_then_background(
                hass.async_create_task(
                    hass.services.async_call(
                        state.domain,
                        SERVICE_PRESS_BUTTON,
                        {ATTR_ENTITY_ID: state.entity_id},
                        context=intent_obj.context,
                        blocking=True,
                    )
                )
            )
            return

        if state.domain == COVER_DOMAIN:
            # on = open
            # off = close
            if service == SERVICE_TURN_ON:
                service_name = SERVICE_OPEN_COVER
            else:
                service_name = SERVICE_CLOSE_COVER

            await self._run_then_background(
                hass.async_create_task(
                    hass.services.async_call(
                        COVER_DOMAIN,
                        service_name,
                        {ATTR_ENTITY_ID: state.entity_id},
                        context=intent_obj.context,
                        blocking=True,
                    )
                )
            )
            return

        if state.domain == LOCK_DOMAIN:
            # on = lock
            # off = unlock
            if service == SERVICE_TURN_ON:
                service_name = SERVICE_LOCK
            else:
                service_name = SERVICE_UNLOCK

            await self._run_then_background(
                hass.async_create_task(
                    hass.services.async_call(
                        LOCK_DOMAIN,
                        service_name,
                        {ATTR_ENTITY_ID: state.entity_id},
                        context=intent_obj.context,
                        blocking=True,
                    )
                )
            )
            return

        if state.domain == VALVE_DOMAIN:
            # on = opened
            # off = closed
            if service == SERVICE_TURN_ON:
                service_name = SERVICE_OPEN_VALVE
            else:
                service_name = SERVICE_CLOSE_VALVE

            await self._run_then_background(
                hass.async_create_task(
                    hass.services.async_call(
                        VALVE_DOMAIN,
                        service_name,
                        {ATTR_ENTITY_ID: state.entity_id},
                        context=intent_obj.context,
                        blocking=True,
                    )
                )
            )
            return

        if not hass.services.has_service(state.domain, service):
            raise intent.IntentHandleError(
                f"Service {service} does not support entity {state.entity_id}"
            )
        
        
            
    async def _run_then_background(self, task: asyncio.Task[Any]) -> None:
        """Run task with timeout to (hopefully) catch validation errors.

        After the timeout the task will continue to run in the background.
        """
        try:
            await asyncio.wait({task}, timeout=self.service_timeout)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            # Task calling us was cancelled, so cancel service call task, and wait for
            # it to be cancelled, within reason, before leaving.
            _LOGGER.debug("Service call was cancelled: %s", task.get_name())
            task.cancel()
            await asyncio.wait({task}, timeout=5)
            raise
        
supported_domain_list = [
    "light",
    "switch",
    "cover",
    "fan",
    "climate",
    "humidifier",
]
        
class TurnDeviceOnIntent(TurnDeviceIntentBase):
    intent_type = "TurnDeviceOn"
    description = (
        "Turns on/opens/presses a device or entity."
    )
    slot_schema = {
        vol.Required("domain"): vol.Any(*supported_domain_list),
        vol.Optional("name"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Optional("except_area"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("floor"): cv.string,
        vol.Optional("preferred_area_id"): cv.string,
        vol.Optional("preferred_floor_id"): cv.string,
    } # type: ignore
    
    service_timeout = 3
    
    async def async_handle(self, intent_obj: intent.Intent) -> JsonObjectType:
        """Get the current state of exposed entities."""
        return await super()._async_handle(intent_obj, "turn_on")
    
class TurnDeviceOffIntent(TurnDeviceIntentBase):
    intent_type = "TurnDeviceOff"
    description = (
        "Turns off/closes a device or entity."
    )
    slot_schema = {
        vol.Required("domain"): vol.Any(*supported_domain_list),
        vol.Optional("name"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Optional("except_area"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("floor"): cv.string,
        vol.Optional("preferred_area_id"): cv.string,
        vol.Optional("preferred_floor_id"): cv.string,
    } # type: ignore
    
    service_timeout = 3
    
    async def async_handle(self, intent_obj: intent.Intent) -> JsonObjectType:
        """Get the current state of exposed entities."""
        return await super()._async_handle(intent_obj, "turn_off")