#!/usr/bin/env python3.8

import asyncio
import json
import logging
import smtplib
import socket
import sys
import time
import traceback

from aioambient import Websocket
from aioambient.errors import WebsocketError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import LoggingEventHandler


class Aw2Graphite:
    class ConfigFileEventHandler(FileSystemEventHandler):
        def __init__(self, aw2graphite, log_level=logging.INFO):
            self.__aw2graphite = aw2graphite
            self._log = logging.getLogger("watchdog")
            self._log.setLevel(log_level)

        def on_modified(self, event):
            if event.src_path or not event.is_directory:
                last_config_load = self.__aw2graphite._state.get('last_load_ts')
                if not last_config_load or last_config_load + 1 < time.time():
                    self._log.info("Detected change in config file")
                    self.__aw2graphite._load_config()

    RT_API_URL = 'https://rt2.ambientweather.net/'

    def __init__(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        )
        logging.getLogger().setLevel(logging.WARNING)
        self._log = logging.getLogger("aw2graphite-rt")
        self._log.setLevel(logging.INFO)

        self.__config_file = "config.json"
        self.__devices = []
        self._state = {}
        self.__config = {}
        self._load_config(startup=True)

        self.__observer = Observer()
        self.__observer.schedule(
            Aw2Graphite.ConfigFileEventHandler(self),
            self.__config_file,
        )
        self.__observer.start()

        if not self.__config["AW_API_KEY"] or \
                not self.__config["AW_APPLICATION_KEY"]:
            raise RuntimeError(
                f'API key and applcation key must be specified ' +
                'in {self.__config_file}'
            )
        self.__load_alerts()
        self.__websocket = Websocket(
            self.__config["AW_APPLICATION_KEY"],
            self.__config["AW_API_KEY"]
        )
        self.__is_connected = False

        self.__websocket.on_connect(self._connect)
        self.__websocket.on_data(self._handle_data)
        self.__websocket.on_disconnect(self._disconnect)
        self.__websocket.on_subscribed(self._subscribed)
        loop = asyncio.get_event_loop()
        loop.create_task(self.__main_loop())
        loop.run_forever()
        self.__observer.stop()
        self.__observer.join()

    def _load_config(self, startup=False):
        self._log.info("Loading config now")
        last_load_ts = time.time()
        with open(self.__config_file, 'r') as f:
            new_config = json.load(f)

        for key, new_value in new_config.items():
            if self.__config.get(key) != new_value:
                if not startup or key == 'LOGLEVEL':
                    if isinstance(new_value, dict) or \
                            isinstance(new_value, list):
                        self._log.info(f"Config item {key} changed from:")
                        self._log.info(
                            json.dumps(self.__config.get(key),indent=2)
                        )
                        self._log.info("to:")
                        self._log.info(json.dumps(new_value, indent=2))
                    else:
                        self._log.info(
                            f"Config item {key} changed from " +
                            f"{self.__config.get(key)} to {new_value}"
                        )
                self.__config[key] = new_value

        new_loglevel = new_config.get('LOGLEVEL')
        if new_loglevel:
            self._log.setLevel(new_loglevel)
        self._state['last_load_ts'] = last_load_ts
        self.__save_state()

    async def __main_loop(self):
        if not self.__is_connected:
            try:
                await self.__websocket.connect()
            except WebsocketError as err:
                traceback.print_exc()
                self._log.error(f"There was a websocket error: {err}")

    def _disconnect(self):
        self.__is_connected = False
        self._log.info("Disconnected from server")

    def _connect(self):
        self._log.info("Connection established")
        self.__is_connected = True

    def _subscribed(self, message):
        devices = message.get('devices')
        for device in devices:
            mac = device.get('macAddress')
            if mac not in self.__devices:
                self.__devices.append(mac)
                self._log.info(f"Added {mac} to my device list")
            else:
                self._log.info(
                    f"Not adding {mac} to my device list because " +
                    "it's already there"
                )

    def __load_alerts(self):
        try:
            state_file = self.__config.get('STATE_FILE')
            with open(state_file, 'r') as f:
                self._log.info(f"Loading state from {state_file}")
                self._state = json.loads(f.read())
        except FileNotFoundError as e:
            self._state = {}
            self.__save_state()

    def __save_state(self):
        state_file = self.__config.get(
            'STATE_FILE',
            self.__config.get(
                'ALERT_STATE_FILE'
            )
        )
        with open(state_file, 'w') as f:
            self._log.debug(f"Writing state to {state_file}:")
            self._log.debug(json.dumps(self._state, indent=2))
            f.write(json.dumps(self._state, indent=2))

    def __update_alert(self, is_alerting, metric_name, alert_msg=None):
        # Check if alerting status has changed
        if self._state.get(metric_name) is not None and \
                self._state.get(metric_name) != is_alerting:
            self._log.debug(f"Metric {metric_name} alert state was " +
                f"{self._state.get(metric_name)} and is now {is_alerting}")
            if is_alerting:
                self._log.debug("That makes this a problem")
                subject_prefix = "[PROBLEM]"
            else:
                self._log.debug("That makes this a recovery")
                subject_prefix = "[RECOVERY]"
            with smtplib.SMTP(
                self.__config.get("SMTP_SERVER"),
                self.__config.get("SMTP_PORT")
            ) as server:
                server.sendmail(
                    self.__config.get("ALERT_FROM"),
                    self.__config.get("ALERT_TO"),
                    f'Subject: {subject_prefix} {metric_name}\n\n{alert_msg}',
                )
        self._state[metric_name] = is_alerting

    def __check_if_alerting(self, metric_name, value):
        threshold = None
        is_alerting = False
        alert_thresholds = self.__config.get("ALERT_THRESHOLDS")
        if alert_thresholds and metric_name in alert_thresholds.keys():
            operator = alert_thresholds[metric_name].get('operator')
            threshold = alert_thresholds[metric_name].get('threshold')
            if threshold is not None:
                if operator == 'gt':
                    is_alerting = value > threshold
                elif operator == 'ge':
                    is_alerting = value >= threshold
                elif operator == 'lt':
                    is_alerting = value < threshold
                elif operator == 'le':
                    is_alerting = value <= threshold
                elif operator == 'eq':
                    is_alerting = value == threshold
                elif operator == 'ne':
                    is_alerting = value != threshold
        self.__update_alert(
            is_alerting=is_alerting,
            metric_name=metric_name,
            alert_msg=f"Weather metric {metric_name} is at value {value}, " +
                f"configured threshold is {threshold}",
        )

    def _handle_data(self, message):
        mac = message.get('macAddress')
        if mac not in self.__devices:
            self._log(
                f"Not handling update for device {mac} since it's not in my " +
                "device list"
            )
            return
        timestamp = int(int(message.get('dateutc'))/1000)
        base_metric = f'weather.{mac}'
        self._log.info(f"Sending metrics for {mac}")
        try:
            sock = socket.socket()
            sock.connect(
                (
                    self.__config.get("CARBON_SERVER"),
                    self.__config.get("CARBON_PORT")
                )
            )
            found_alert = False
            for metric_name in message.keys():
                rt_metrics = self.__config.get('RT_METRICS')
                if rt_metrics and metric_name not in rt_metrics:
                    continue
                try:
                    alert = False
                    value = message.get(metric_name)
                    if metric_name != 'dateutc' and \
                            isinstance(value, (int, float)):
                        s = f'{base_metric}.{metric_name} {value} {timestamp}\n'
                        #self._log.debug("Sending metric:")
                        #self._log.debug(s)
                        sock.send(s.encode())
                        self.__check_if_alerting(metric_name, value)
                except Exception as e:
                    self._log.exception(e)
        except Exception as e:
            self._log.exception(e)
        finally:
            self.__save_state()
            sock.close()


if __name__ == '__main__':
    while True:
        aw2graphite = Aw2Graphite()
        time.sleep(1)
