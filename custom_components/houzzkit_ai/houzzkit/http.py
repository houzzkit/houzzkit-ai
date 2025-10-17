import hashlib
from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST
from homeassistant.helpers import device_registry as dr
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from ..const import DOMAIN

async def async_setup_https(hass: HomeAssistant):
    this_data = hass.data.setdefault(DOMAIN, {})
    if this_data.get("https_setup"):
        return
    this_data["https_setup"] = True
    hass.http.register_view(HouzzkitSetupView)
    hass.http.register_view(HouzzkitRemoveView)
    hass.http.register_view(HouzzkitSetNameView)


class HouzzkitHttpView(HomeAssistantView):
    requires_auth = False

    async def check_sign(self, request: web.Request, speak_id=None):
        hass = request.app[KEY_HASS]
        params = request.query
        if request.method in ("PUT", "POST"):
            params = await request.json() or {}
        if not speak_id:
            speak_id = params.get("speak_id") or request.query.get("speak_id", "")
        entry = None
        for ent in hass.config_entries.async_loaded_entries(DOMAIN):
            if speak_id == ent.data.get("speak_id"):
                entry = ent
                break
        if not entry:
            return None
        salt = request.headers.get("Salt", "")
        ret = request.headers.get("Authorization") == calculate_sign(
            request.path,
            params,
            entry.data.get("mac", "").lower(),
            salt,
        )
        return entry if ret else False


class HouzzkitSetupView(HouzzkitHttpView):
    url = "/api/houzzkit-ai/setup/qrcode"
    name = "api:houzzkit-ai:setup-qrcode"

    async def post(self, request: web.Request):
        hass = request.app[KEY_HASS]
        this_data = hass.data.setdefault(DOMAIN, {})
        if not (uuid := request.query.get("uuid")):
            return self.json_message("uuid missing")
        if uuid not in this_data:
            return self.json_message("uuid invalid")
        setup_data = await request.json() or {}
        if not setup_data.get(CONF_HOST):
            return self.json_message("host missing")
        this_data[uuid] = setup_data
        return self.json_message("ok")

class HouzzkitRemoveView(HouzzkitHttpView):
    url = "/api/houzzkit-ai/remove"
    name = "api:houzzkit-ai:remove"

    async def delete(self, request: web.Request):
        hass = request.app[KEY_HASS]
        if not (speak_id := request.query.get("speak_id")):
            return self.json_message("speak_id missing")
        entry = await self.check_sign(request, speak_id)
        if not entry:
            return self.json_message("params error")
        await hass.config_entries.async_remove(entry.entry_id)
        return self.json_message("ok")

class HouzzkitSetNameView(HouzzkitHttpView):
    url = "/api/houzzkit-ai/update/speakname"
    name = "api:houzzkit-ai:update:speakname"

    async def post(self, request: web.Request):
        hass = request.app[KEY_HASS]
        entry = await self.check_sign(request)
        if not entry:
            return self.json_message("params error")
        data = await request.json() or {}
        if not (name := data.get("speak_name")):
            return self.json_message("speak_name missing")
        mac = entry.data.get("mac")
        device_registry = dr.async_get(hass)
        device_entry = device_registry.async_get_device(
            connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        )
        if not device_entry:
            return self.json_message("device not found")
        device_registry.async_update_device(device_entry.id, name=name)
        hass.config_entries.async_update_entry(entry, title=name)
        return self.json_message("ok")


def calculate_sign(uri, params, mac, salt):
    """
    签名算法:
    1. n = md5(uri)
    2. 拼接参数字符串并计算 m = md5(参数字符串)
    3. response = md5(m + n + mac + salt)
    """
    # 步骤1: 计算 n = md5(uri)
    n = hashlib.md5(uri.encode('utf-8')).hexdigest()

    # 步骤2: 拼接参数并计算 m = md5(参数字符串)
    # 将参数排序后拼接为 key=value 格式
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    param_str = '&'.join([f"{k}={v}" for k, v in sorted_params])
    m = hashlib.md5(param_str.encode('utf-8')).hexdigest()

    # 步骤3: 计算最终摘要
    response_str = f"{m}{n}{mac}{salt}"
    return hashlib.md5(response_str.encode('utf-8')).hexdigest()
