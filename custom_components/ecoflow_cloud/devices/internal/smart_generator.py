import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.util import dt

from custom_components.ecoflow_cloud.api import EcoflowApiClient
from custom_components.ecoflow_cloud.api.message import JSONMessage, Message
from custom_components.ecoflow_cloud.binary_sensor import MiscBinarySensorEntity
from custom_components.ecoflow_cloud.devices import BaseDevice, BaseInternalDevice, const
from custom_components.ecoflow_cloud.devices.data_holder import PreparedData
from custom_components.ecoflow_cloud.entities import BaseSensorEntity
from custom_components.ecoflow_cloud.sensor import (
    MilliampSensorEntity,
    MilliVoltSensorEntity,
    MiscSensorEntity,
    OutWattsSensorEntity,
    QuotaScheduledStatusSensorEntity,
    TempSensorEntity,
    WattsSensorEntity,
)
from custom_components.ecoflow_cloud.switch import EnabledEntity

_MODULE_TYPE = 2

# The generator only pushes telemetry on /app/device/property/<sn> while the
# EcoFlow app has it open. Otherwise the only way to get data is to ask for it
# with a latestQuotas request, so we poll on a fixed interval.
_QUOTA_REFRESH_SEC = 30

# Service log from the app's maintenance screen (HTTP, not MQTT). It only
# changes when someone marks a service done in the app, so poll it rarely.
_MAINTENANCE_REFRESH_SEC = 3600

# Service log key -> title. The log has more items (fuel filter, fuel line,
# valve clearance, ...), only these are worth a sensor.
_MAINTENANCE_ITEMS: dict[str, str] = {
    "oil": "Oil Change",
    "elementClean": "Air Filter Cleaning",
    "bowl": "Sediment Cup",
    "plug": "Spark Plug",
    "arrester": "Spark Arrester",
}

_LOGGER = logging.getLogger(__name__)


class _FuelLevelSensorEntity(BaseSensorEntity):
    # Not SensorDeviceClass.BATTERY: this is the fuel tank, not a battery.
    _attr_icon = "mdi:gas-station"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT


class _MinutesSensorEntity(BaseSensorEntity):
    # RemainSensorEntity clamps to 5000 min, too low for the generator's
    # remaining time (up to 5999) and for its cumulative motor run time.
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _update_value(self, val: Any) -> bool:
        return super()._update_value(max(int(val), 0))


class _RunTimeSensorEntity(_MinutesSensorEntity):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING


class _MaintenanceTimeSensorEntity(BaseSensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:wrench-clock"


class _MaintenanceHoursSensorEntity(BaseSensorEntity):
    # Engine hours, not wall-clock hours.
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wrench-clock"


def _maintenance_params(log: dict[str, Any]) -> dict[str, Any]:
    """Flatten the service log into sensor params.

    The log has the engine run time in seconds (``totalTime``) and, per item,
    when the service was marked done (``updateTime``, epoch ms) and the run
    time at that moment (``totalTime``).
    """
    params: dict[str, Any] = {}
    run_time = log.get("totalTime")
    for key in _MAINTENANCE_ITEMS:
        item = log.get(key)
        if not isinstance(item, dict):
            continue
        done_ms = item.get("updateTime")
        if isinstance(done_ms, int | float) and done_ms > 0:
            done: datetime = dt.utc_from_timestamp(done_ms / 1000)
            params[f"maintenance.{key}.lastTime"] = done
        done_at = item.get("totalTime")
        # Some items carry 0 or a run time larger than the engine's total
        # (looks like a placeholder); hours since service is unknown then.
        if isinstance(run_time, int | float) and isinstance(done_at, int | float) and 0 < done_at <= run_time:
            params[f"maintenance.{key}.hoursSince"] = round((run_time - done_at) / 3600, 1)
    return params


class _GeneratorStatusSensorEntity(QuotaScheduledStatusSensorEntity):
    """Status sensor that also refreshes the service log over HTTP."""

    def __init__(self, client: EcoflowApiClient, device: BaseDevice):
        # Same title/key as QuotaStatusSensorEntity so the existing
        # "Status" entity is kept.
        super().__init__(client, device, _QUOTA_REFRESH_SEC, title="Status", key="status")
        self._last_maintenance_poll: datetime | None = None

    @override
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()

        # Only the private (app) API has this endpoint.
        if getattr(self._client, "generator_maintenance_log", None) is None:
            return
        now = dt.utcnow()
        if (
            self._last_maintenance_poll is not None
            and (now - self._last_maintenance_poll).total_seconds() < _MAINTENANCE_REFRESH_SEC
        ):
            return
        self._last_maintenance_poll = now
        self.hass.async_create_background_task(
            self._refresh_maintenance(), f"ecoflow generator maintenance {self._device.device_info.sn}"
        )

    async def _refresh_maintenance(self) -> None:
        sn = self._device.device_info.sn
        try:
            log = await self._client.generator_maintenance_log(sn)  # type: ignore[attr-defined]
        except Exception as error:
            # Retried on the next interval.
            _LOGGER.warning("Failed to fetch maintenance log for %s: %s", sn, error)
            return
        params = _maintenance_params(log)
        if params:
            # is_auto=False: an HTTP answer does not prove the generator is online.
            self._device.data.add_data(PreparedData(None, {"params": params}, log, is_auto=False))


def _command(operate_type: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"moduleType": _MODULE_TYPE, "operateType": operate_type, "params": params}


class SmartGenerator(BaseInternalDevice):
    """EcoFlow Smart Generator (gasoline and Dual Fuel)."""

    @override
    def get_quota_message(self) -> Message:
        # Matches what the EcoFlow app sends (sniffed): version 1.0 and no
        # moduleType. The base class sends version 1.1 with moduleType 0.
        return JSONMessage({"operateType": "latestQuotas", "params": {}})

    @override
    def sensors(self, client: EcoflowApiClient) -> Sequence[SensorEntity]:
        return [
            OutWattsSensorEntity(client, self, "pd.acPower", const.AC_OUT_POWER),
            OutWattsSensorEntity(client, self, "pd.dcPower", const.DC_OUT_POWER),
            OutWattsSensorEntity(client, self, "pd.totalPower", const.TOTAL_OUT_POWER),
            WattsSensorEntity(client, self, "pd.oilMaxOutPower", const.GEN_MAX_OUTPUT_POWER),
            _FuelLevelSensorEntity(client, self, "pd.oilVal", "Fuel Level"),
            _MinutesSensorEntity(client, self, "pd.remainTime", const.REMAINING_TIME),
            _RunTimeSensorEntity(client, self, "pd.motorUseTime", "Motor Run Time"),
            MilliVoltSensorEntity(client, self, "pd.acVol", const.AC_OUT_VOLT),
            MilliVoltSensorEntity(client, self, "pd.dcVol", const.DC_OUT_VOLTAGE),
            MilliampSensorEntity(client, self, "pd.acCur", "AC Out Current"),
            MilliampSensorEntity(client, self, "pd.dcCur", "DC Out Current"),
            TempSensorEntity(client, self, "pd.temp", const.TEMPERATURE),
            MiscSensorEntity(client, self, "pd.sysMode", "System Mode", diagnostic=True),
            MiscSensorEntity(client, self, "pd.errCode", const.ERROR_CODE, diagnostic=True),
            MiscSensorEntity(client, self, "pd.type", "Generator Type", False, diagnostic=True),
            MiscSensorEntity(client, self, "pd.num", "Unit Number", False, diagnostic=True),
            MiscSensorEntity(client, self, "pd.ver", "Firmware Version", False, diagnostic=True),
            MiscSensorEntity(client, self, "pd.cellId", "Cell ID", False, diagnostic=True),
            *self._maintenance_sensors(client),
            _GeneratorStatusSensorEntity(client, self),
        ]

    def _maintenance_sensors(self, client: EcoflowApiClient) -> list[SensorEntity]:
        sensors: list[SensorEntity] = []
        for key, title in _MAINTENANCE_ITEMS.items():
            sensors.append(_MaintenanceTimeSensorEntity(client, self, f"maintenance.{key}.lastTime", f"Last {title}"))
            sensors.append(
                _MaintenanceHoursSensorEntity(client, self, f"maintenance.{key}.hoursSince", f"Hours Since {title}")
            )
        return sensors

    @override
    def binary_sensors(self, client: EcoflowApiClient) -> Sequence[BinarySensorEntity]:
        return [
            MiscBinarySensorEntity(client, self, "pd.dcState", "DC Output State"),
        ]

    @override
    def numbers(self, client: EcoflowApiClient) -> Sequence[NumberEntity]:
        return []

    @override
    def switches(self, client: EcoflowApiClient) -> Sequence[SwitchEntity]:
        return [
            EnabledEntity(
                client,
                self,
                "pd.motorState",
                "Generator Motor",
                lambda value: _command("motorCtrl", {"enable": value}),
            ),
            EnabledEntity(
                client,
                self,
                "pd.acState",
                const.AC_ENABLED,
                lambda value: _command("acCtrl", {"enable": value}),
            ),
        ]

    @override
    def selects(self, client: EcoflowApiClient) -> Sequence[SelectEntity]:
        return []
