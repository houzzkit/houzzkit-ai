import asyncio
from enum import Enum
from typing import Any
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


    
class TurnDeviceOnIntent(intent.IntentHandler):
    intent_type = "TurnDeviceOn"
    description = (
        "Turns on/opens/presses a device or entity."
    )
    slot_schema = {
        vol.Required("domain"): intent.non_empty_string,
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
        slots = self.async_validate_slots(intent_obj.slots)
        domain: str = slots.get("domain", {}).get("value")
        name: str | None  = slots.get("name", {}).get("value")
        area_name: str | None = slots.get("area", {}).get("value")
        floor_name: str | None = slots.get("floor", {}).get("value")
        except_area: list[str] | None = slots.get("except_area", {}).get("value")
        
        _LOGGER.info(
            f"TurnDeviceOn params: slots={slots} "
        )
        
        hass = intent_obj.hass
        match_constraints = intent.MatchTargetsConstraints(
            name=name,
            area_name=area_name,
            floor_name=floor_name,
            domains={domain},
            assistant=intent_obj.assistant,
            single_target=False,
        )
        match_preferences = intent.MatchTargetsPreferences(
            area_id=slots.get("preferred_area_id", {}).get("value"),
            floor_id=slots.get("preferred_floor_id", {}).get("value"),
        )
        match_result = intent.async_match_targets(
            hass, match_constraints, match_preferences
        )
        if not match_result.is_match:
            raise intent.MatchFailedError(
                result=match_result, constraints=match_constraints
            )
        assert match_result.states
        for state in match_result.states:
            await self.handle_match_target(intent_obj, state, "turn_on")

        return {
            "success": True,
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