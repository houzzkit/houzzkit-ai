import time
import yaml
import logging
import aiofiles
import voluptuous as vol

from pathlib import Path
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.const import (
    Platform, ATTR_ENTITY_ID,
    CONF_ID, SERVICE_RELOAD,
    CONF_ALIAS, CONF_MODE, CONF_TRIGGER, CONF_ACTION,
)
from homeassistant.components.automation import (
    DOMAIN as AUTOMATION_DOMAIN,
    CONF_TRIGGERS, CONF_ACTIONS,
)
from homeassistant.components.climate.const import (
    HVAC_MODES,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_FAN_MODE,
    ATTR_HVAC_MODE,
    ATTR_FAN_MODES,
    ATTR_FAN_MODE,
)
from homeassistant.util.percentage import percentage_to_ordered_list_item

from .houzzkit import get_entities
from .intent_adjust_attribute import AdjustDeviceAttributeIntent
from .intent_live_context import HouzzkitGetLiveContextIntent

_LOGGER = logging.getLogger(__name__)


async def async_setup_intents(hass: HomeAssistant):
    """Set up the intents."""
    intent.async_register(hass, ClimateSetHvacModeIntent())
    intent.async_register(hass, ClimateSetFanModeIntent())
    intent.async_register(hass, CreateAlarmClockIntent())
    intent.async_register(hass, CreateCountdownAlarmClockIntent())
    intent.async_register(hass, AdjustDeviceAttributeIntent())
    intent.async_register(hass, HouzzkitGetLiveContextIntent())


class ClimateSetHvacModeIntent(intent.IntentHandler):
    intent_type = "ClimateSetHvacMode"
    description = "Sets the target hvac mode of a climate device or entity"
    slot_schema = {
        vol.Required(ATTR_HVAC_MODE): vol.Any(*HVAC_MODES),
        vol.Required("domain"): vol.Any("climate"),
        vol.Optional("area"): intent.non_empty_string,
        vol.Optional("name"): intent.non_empty_string,
        vol.Optional("floor"): intent.non_empty_string,
        vol.Optional("preferred_area_id"): cv.string,
        vol.Optional("preferred_floor_id"): cv.string,
    }
    platforms = {Platform.CLIMATE}

    async def async_handle(self, intent_obj: intent.Intent):
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        mode = slots[ATTR_HVAC_MODE]["value"]
        name = slots.get("name", {}).get("value")
        area_name = slots.get("area", {}).get("value")
        floor_name = slots.get("floor", {}).get("value")

        match_constraints = intent.MatchTargetsConstraints(
            name=name,
            area_name=area_name,
            floor_name=floor_name,
            domains=[Platform.CLIMATE],
            assistant=intent_obj.assistant,
            single_target=True,
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
        state = match_result.states[0]

        await hass.services.async_call(
            Platform.CLIMATE,
            SERVICE_SET_HVAC_MODE,
            target={ATTR_ENTITY_ID: state.entity_id},
            service_data={ATTR_HVAC_MODE: mode},
            blocking=True,
        )

        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_states(matched_states=[state])
        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    name=state.name,
                    id=state.entity_id,
                ),
            ],
        )
        return response


class ClimateSetFanModeIntent(intent.IntentHandler):
    intent_type = "ClimateSetFanMode"
    description = (
        "Sets the target fan level of a climate device or entity. "
        "Users may describe fan level using terms like high, medium, low, 1st gear, level 7, etc., "
        "and these need to be converted to percentages and returned. "
        "Use 0 to represent automatic wind speed. "
    )
    slot_schema = {
        vol.Required(ATTR_FAN_MODE): vol.All(vol.Coerce(int), vol.Range(0, 100)),
        vol.Required("domain"): vol.All("climate"),
        vol.Optional("area"): intent.non_empty_string,
        vol.Optional("name"): intent.non_empty_string,
        vol.Optional("floor"): intent.non_empty_string,
        vol.Optional("preferred_area_id"): cv.string,
        vol.Optional("preferred_floor_id"): cv.string,
    }
    platforms = {Platform.CLIMATE}

    async def async_handle(self, intent_obj: intent.Intent):
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        mode = slots[ATTR_FAN_MODE]["value"]
        name = slots.get("name", {}).get("value")
        area_name = slots.get("area", {}).get("value")
        floor_name = slots.get("floor", {}).get("value")

        match_constraints = intent.MatchTargetsConstraints(
            name=name,
            area_name=area_name,
            floor_name=floor_name,
            domains=[Platform.CLIMATE],
            assistant=intent_obj.assistant,
            single_target=True,
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
        state = match_result.states[0]
        fan_modes = state.attributes.get(ATTR_FAN_MODES, [])
        if not fan_modes:
            raise intent.IntentHandleError("This climate device does not have fan modes")
        fan_mode = None
        if mode == 0:
            for m in fan_modes:
                if str(m).lower() in ["auto", "smart", "自动", "智能"]:
                    fan_mode = m
                    break
        if fan_mode is None:
            fan_mode = percentage_to_ordered_list_item(fan_modes, mode)

        await hass.services.async_call(
            Platform.CLIMATE,
            SERVICE_SET_FAN_MODE,
            target={ATTR_ENTITY_ID: state.entity_id},
            service_data={ATTR_FAN_MODE: fan_mode},
            blocking=True,
        )

        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_states(matched_states=[state])
        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    name=state.name,
                    id=state.entity_id,
                ),
            ],
        )
        return response


DATA_KEY = "houzzkit_alarm_clock"
repeat_SINGLE = "single"
MOD_CYCLE = "cycle"
REPEAT_EVERYDAY = "everyday"
REPEAT_WORKDAY = "weekday"


class CreateAlarmClockIntent(intent.IntentHandler):
    """Handle 创建循环闹钟 intents."""
    # Type of intent to handle
    intent_type = "HOUZZkitCreateAlarmClock"

    description = "Create Alarm Clock"

    # Optional. A validation schema for slots
    slot_schema = {
        vol.Required("trigger_time"): cv.string,
        vol.Required("alias"): cv.string,
        vol.Required("repeat"): vol.All(cv.ensure_list, [vol.In([REPEAT_EVERYDAY, REPEAT_WORKDAY])]),
        # vol.Required("speaker_id"): cv.string
    }

    async def async_handle(self, intent_obj):
        """Handle the intent. """
        slots = self.async_validate_slots(intent_obj.slots)
        trigger_time = slots["trigger_time"]["value"]
        alias = slots["alias"]["value"]
        repeat = slots["repeat"]["value"]
        speak_id = slots["_speaker_id"]["value"]
        week_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        if repeat == REPEAT_WORKDAY:
            week_day = ["mon", "tue", "wed", "thu", "fri"]
        trigger = [{
            CONF_TRIGGER: 'time',
            'at': trigger_time,
            'weekday': week_day
        }]
        success, message = await create_alarm_clock_auto(intent_obj.hass, trigger, alias, speak_id, True)
        if success is False:
            raise intent.IntentHandleError(
                f"创建闹钟失败: {message}",
                response_key="creation_failed"
            )
        response = intent_obj.create_response()
        # 格式化工作日显示
        day_names = {
            "mon": "周一",
            "tue": "周二",
            "wed": "周三",
            "thu": "周四",
            "fri": "周五",
            "sat": "周六",
            "sun": "周日"
        }
        formatted_days = ", ".join([day_names[d] for d in week_day])
        response.async_set_speech(
            f"已成功创建闹钟 '{alias}'，"
            f"将在每周 {formatted_days} "
            f"的 {trigger_time} 触发"
        )
        return response


class CreateCountdownAlarmClockIntent(intent.IntentHandler):
    """Handle 倒计时闹钟."""
    # Type of intent to handle
    intent_type = "HOUZZkitCreateCountdownAlarmClock"

    description = "Create Countdown Alarm Clock"

    # Optional. A validation schema for slots
    slot_schema = {
        vol.Required("hour"): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=23)
        ),
        vol.Required("minute"): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=59)
        ),
        vol.Required("second"): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=59)
        ),
        vol.Required("alias"): cv.string,
        # vol.Required("speaker_id"): cv.string
    }

    async def async_handle(self, intent_obj):
        """Handle the intent. """
        slots = self.async_validate_slots(intent_obj.slots)
        alias = slots["alias"]["value"]
        hours = slots["hour"]["value"]
        minutes = slots["minute"]["value"]
        seconds = slots["second"]["value"]
        speak_id = slots["_speaker_id"]["value"]
        trigger = {
            CONF_TRIGGER: 'time_pattern'
        }
        if hours > 0:
            trigger['hours'] = f"/{str(hours)}"
        if minutes > 0:
            trigger['minutes'] = f"/{str(minutes)}"
        if seconds > 0:
            trigger['seconds'] = f"/{str(seconds)}"
        triggers = [trigger]
        success, message = await create_alarm_clock_auto(intent_obj.hass, triggers, alias, speak_id, False)
        response = intent_obj.create_response()
        if success is False:
            raise intent.IntentHandleError(
                f"创建闹钟失败: {message}",
                response_key="creation_failed"
            )
        # 格式化工作日显示
        response.async_set_speech(
            f"已成功创建闹钟 '{alias}'，"
            f"的 {hours}:{minutes}:{seconds} 后触发"
        )
        return response

async def create_alarm_clock_auto(hass, triggers: list[dict], alias, speak_id, repeat: bool) -> tuple[bool, str]:
    """
        倒计时闹钟
        trigger = [{
            'trigger': 'time_pattern',
            'seconds': '/20',
        }]
        res = await create_alarm_clock_auto(hass, trigger, "闹钟1", data.get("speak_id"), False)
        循环闹钟
        week_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        trigger = [{
            CONF_TRIGGER: 'time',
            'at': trigger_time,
            'weekday': week_day
        }]
        res = await create_alarm_clock_auto(hass, trigger, "闹钟1", data.get("speak_id"), True)
    """
    config_key = str(int(time.time()))
    entries = get_entities(hass, speak_id)
    entry_id = ""
    for entry in entries:
        if entry.name == "Alarm":
            entry_id = entry.entity_id
    if len(entry_id) == 0:
        return False, "未找到对应的可操作设备"
    action = [{
        CONF_ACTION: 'button.press',
        'metadata': {},
        'data': {},
        'target': {
            'entity_id': entry_id
        }
    }]
    if repeat is False:
        action.append({
            CONF_ACTION: 'automation.toggle',
            'metadata': {},
            'data': {},
            'target': {
                'entity_id': '{{ this.entity_id }}'
            }
        })
    automations_config = {
        CONF_ID: f"{config_key}",
        CONF_ALIAS: alias,
        'description': '',
        CONF_TRIGGERS: triggers,
        'conditions': [],
        CONF_ACTIONS: action,
        CONF_MODE: 'single'
    }
    automations_path = Path(hass.config.path("automations.yaml"))
    try:
        # 读取现有配置（如果存在）
        if automations_path.exists():
            async with aiofiles.open(automations_path, 'r') as f:
                content = await f.read()
                existing_config = yaml.safe_load(content) or []
        else:
            existing_config = []
        #合并配置（避免重复）
        # 检查是否已经存在相同ID的自动化
        existing_ids = {automation.get(CONF_ID) for automation in existing_config}
        if automations_config.get(CONF_ID) not in existing_ids:
            existing_config.append(automations_config)

        async with aiofiles.open(automations_path, 'w') as f:
            await f.write(yaml.dump(existing_config, default_flow_style=False, sort_keys=False))
        _LOGGER.info("自动化配置已更新到 automations.yaml")
        # 5. 重新加载自动化组件
        await hass.services.async_call(
            AUTOMATION_DOMAIN, SERVICE_RELOAD, {CONF_ID: config_key}
        )
        _LOGGER.info("自动化组件已重新加载")
    except Exception as e:
        _LOGGER.error("更新自动化配置时出错: %s", str(e))
        return False, str(e)
    return True, ""
