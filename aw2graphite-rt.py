#!/usr/bin/env python3.8

import asyncio
import json
import logging
import smtplib
import socket

from aioambient import Websocket
from aioambient.errors import WebsocketError


class Aw2Graphite:
    RT_API_URL = 'https://rt2.ambientweather.net/'

    def __init__(self):
        self._log = logging.getLogger("aw2graphite-rt")
        self._log.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        self._log.addHandler(ch)

        self._devices = []
        with open('./config.json', 'r') as f:
            config = json.load(f)
            loglevel = config.get('LOGLEVEL')
            if loglevel:
                self._log.setLevel(loglevel)
            self.api_key = config.get('AW_API_KEY')
            self.app_key = config.get('AW_APPLICATION_KEY')
            self.carbon_server = config.get('CARBON_SERVER')
            self.carbon_port = config.get('CARBON_PORT')
            self.smtp_server = config.get('SMTP_SERVER')
            self.smtp_port = config.get('SMTP_PORT')
            self.alert_from = config.get('ALERT_FROM')
            self.alert_to = config.get('ALERT_TO')
            self.alert_state_file = config.get('ALERT_STATE_FILE')
            self.alert_thresholds = config.get('ALERT_THRESHOLDS')

        if 'AW_API_KEY' not in config.keys() or 'AW_APPLICATION_KEY' not in config.keys():
            raise RuntimeError(
                'API key and applcation key must be specified in config.json'
            )
        self.alerts = {}
        self.load_alerts()
        self.__websocket = Websocket(self.app_key, self.api_key)
        self.__is_connected = False

        self.__websocket.on_connect(self.connect)
        self.__websocket.on_data(self.handle_data)
        self.__websocket.on_disconnect(self.disconnect)
        self.__websocket.on_subscribed(self.subscribed)
        loop = asyncio.get_event_loop()
        loop.create_task(self.__main_loop())
        loop.run_forever()

    async def __main_loop(self):
        if not self.__is_connected:
            try:
                await self.__websocket.connect()
            except WebsocketError as err:
                self._log.error(f"There was a websocket error: {err}")
                self.__is_connected = False
                loop.create_task(self.__main_loop())

    def disconnect(self):
        self.__is_connected = False
        self._log.info("Disconnected from server")
        loop = asyncio.get_event_loop()
        loop.create_task(self.__main_loop())

    def connect(self):
        self._log.info("Connection established")
        self.__is_connected = True

    def subscribed(self, message):
        devices = message.get('devices')
        for device in devices:
            mac = device.get('macAddress')
            if mac not in self._devices:
                self._devices.append(mac)
                self._log.info(f"Added {mac} to my device list")
            else:
                self._log.info("Not adding {mac} to my device list because it's already there")

    def load_alerts(self):
        try:
            with open(self.alert_state_file, 'r') as f:
                self._log.info(f"Loading alerts from {self.alert_state_file}")
                self.alerts = json.loads(f.read())
        except FileNotFoundError as e:
            self.alerts = {}
            self.save_alerts()

    def save_alerts(self):
        with open(self.alert_state_file, 'w') as f:
            self._log.debug(f"Writing alerts to {self.alert_state_file}:")
            self._log.debug(json.dumps(self.alerts, indent=2))
            f.write(json.dumps(self.alerts, indent=2))

    def update_alert(self, is_alerting, metric_name, alert_msg=None):
        # Check if alerting status has changed
        if self.alerts.get(metric_name) != is_alerting:
            if is_alerting:
                subject_prefix = "PROBLEM: "
            else:
                subject_prefix = "RECOVERY: "
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.sendmail(
                    self.alert_from,
                    self.alert_to,
                    f'Subject: {subject_prefix}: {metric_name}\n\n{alert_msg}',
                )
        self.alerts[metric_name] = is_alerting

    def check_if_alerting(self, metric_name, value):
        threshold = None
        is_alerting = False
        if self.alert_thresholds and metric_name in self.alert_thresholds.keys():
            operator = self.alert_thresholds[metric_name].get('operator')
            threshold = self.alert_thresholds[metric_name].get('threshold')
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
        self.update_alert(
            is_alerting=is_alerting,
            metric_name=metric_name,
            alert_msg=f"Weather metric {metric_name} is at value {value}, configured threshold is {threshold}",
        )

    def handle_data(self, message):
        mac = message.get('macAddress')
        if mac not in self._devices:
            self._log(f"Not handling update for device {mac} since it's not in my device list")
            return
        timestamp = int(int(message.get('dateutc'))/1000)
        base_metric = f'weather.{mac}'
        self._log.info(f"Sending metrics for {mac}")
        try:
            sock = socket.socket()
            sock.connect((self.carbon_server, self.carbon_port))
            found_alert = False
            for metric_name in message.keys():
                try:
                    alert = False
                    value = message.get(metric_name)
                    if metric_name != 'dateutc' and isinstance(value, (int, float)):
                        s = f'{base_metric}.{metric_name} {value} {timestamp}\n'
                        #self._log.debug("Sending metric:")
                        #self._log.debug(s)
                        sock.send(s.encode())
                        self.check_if_alerting(metric_name, value)
                except Exception as e:
                    self._log.exception(e)
        except Exception as e:
            self._log.exception(e)
        finally:
            self.save_alerts()
            sock.close()

if __name__ == '__main__':
    aw2graphite = Aw2Graphite()
