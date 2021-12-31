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
        self._log.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        self._log.addHandler(ch)

        self._devices = []
        with open('./config.json', 'r') as f:
            config = json.load(f)
            self.api_key = config.get('AW_API_KEY')
            self.app_key = config.get('AW_APPLICATION_KEY')
            self.carbon_server = config.get('CARBON_SERVER')
            self.carbon_port = config.get('CARBON_PORT')
            self.smtp_server = config.get('SMTP_SERVER')
            self.smtp_port = config.get('SMTP_PORT')
            self.alert_from = config.get('ALERT_FROM')
            self.alert_to = config.get('ALERT_TO')
            self.alert_state_file = config.get('ALERT_STATE_FILE')

        if 'AW_API_KEY' not in config.keys() or 'AW_APPLICATION_KEY' not in config.keys():
            raise RuntimeError(
                'API key and applcation key must be specified in config.json'
            )
        self.__websocket = Websocket(self.app_key, self.api_key)

        self.__websocket.on_connect(self.connect)
        self.__websocket.on_data(self.data)
        self.__websocket.on_disconnect(self.disconnect)
        self.__websocket.on_subscribed(self.subscribed)
        loop = asyncio.get_event_loop()
        loop.create_task(self.__main_loop())
        loop.run_forever()

    async def __main_loop(self):
        try:
            await self.__websocket.connect()
        except WebsocketError as err:
            self._log.error(f"There was a websocket error: {err}")

    def disconnect(self):
        self._log.info("Disconnected from server")

    def connect(self):
        self._log.info("Connection established")

    def subscribed(self, data):
        devices = data.get('devices')
        for device in devices:
            mac = device.get('macAddress')
            if mac not in self._devices:
                self._devices.append(mac)
                self._log.info(f"Added {mac} to my device list")
            else:
                self._log.info("Not adding {mac} to my device list because it's already there")

    def data(self, message):
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
                    if metric_name == 'dateutc':
                        continue
                    elif metric_name == 'battout' and value != 1:
                        alert = f"Outside weather station battery needs replacing (battout = {value})"
                    elif metric_name == 'batt_co2' and value != 1:
                        alert = f"Inside weather station battery needs replacing (batt_co2 = {value})"
                    if isinstance(value, (int, float)):
                        s = f'{base_metric}.{metric_name} {value} {timestamp}\n'
                        #self._log.info("Sending metric:")
                        #self._log.info(s)
                        sock.send(s.encode())
                    if alert:
                        found_alert = True
                        already_alerted = False
                        try:
                            with open(self.alert_state_file) as f:
                                if alert == f.readline():
                                    already_alerted = True
                        except Exception as e:
                            pass
                        if already_alerted:
                            self._log.info(f"Already alerted for alert {alert} so not sending email")
                        else:
                            self._log.info(f"Sending alert email to {self.alert_to} with content: {alert}")
                            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                                server.sendmail(
                                    self.alert_from,
                                    self.alert_to,
                                    f'Subject: {alert}\n\n{alert}'
                                )
                            with open(self.alert_state_file, 'w') as f:
                                self._log.info(f"Writing alert to {self.alert_state_file}: ({alert})")
                                f.write(alert)
                except Exception as e:
                    self._log.exception(e)
            if not found_alert:
                with open(self.alert_state_file, 'w') as f:
                    self._log.info(f"Blanking {self.alert_state_file}")
                    f.write('')
        except Exception as e:
            self._log.exception(e)
        finally:
            sock.close()


if __name__ == '__main__':
    aw2graphite = Aw2Graphite()
