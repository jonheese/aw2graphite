#!/usr/bin/env python3

import json
import logging
import requests
import socket
import time
import urllib.parse


class Aw2Graphite:
    API_URL = 'https://rt.ambientweather.net/v1'

    def __init__(self):
        self._log = logging.getLogger("aw2graphite")
        self._log.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        self._log.addHandler(ch)

        with open('./config.json', 'r') as f:
            config = json.load(f)
            self.api_key = config.get('AW_API_KEY')
            self.app_key = config.get('AW_APPLICATION_KEY')
            self.carbon_server = config.get('CARBON_SERVER')
            self.carbon_port = config.get('CARBON_PORT')

        if 'AW_API_KEY' not in config.keys() or 'AW_APPLICATION_KEY' not in config.keys():
            raise RuntimeError(
                'API key and applcation key must be specified in config.json'
            )
        self.default_headers = {
            'accept': 'application/json',
        }
        self.devices = []

    def do_api_call(
            self,
            endpoint=None,
            method='GET',
            payload=None,
            params=None,
            headers=None,
    ):
        if not headers:
            headers = self.default_headers
        if endpoint is None:
            raise RuntimeError('API endpoint must be provided')
        url = f'{self.API_URL}/{endpoint}?applicationKey={self.app_key}&apiKey={self.api_key}'
        if params:
            url = f"{url}&{urllib.parse.urlencode(params)}"
        if method == 'GET':
            r = requests.get(
                url,
                headers=headers,
            )
        elif method == 'POST':
            r = requests.post(
                url,
                data=payload,
                headers=headers,
            )
        else:
            raise RuntimeError(f'Method {method} unsupported')
        if r.status_code != requests.codes.ok:
            raise RuntimeError(
                f'Error encountered performing {method} to {url} ' +
                'with headers:\n' + json.dumps(headers, indent=2) + '\n' +
                json.dumps(r.json(), indent=2)
            )
        return r.json()

    def get_devices(self):
        self.devices = self.do_api_call(
            endpoint='devices',
        )
        #self._log.info(json.dumps(self.devices, indent=2))

    def insert_data(self):
        base_metric = 'weather'
        if not self.devices:
            self.get_devices()
            time.sleep(1)
        try:
            sock = socket.socket()
            sock.connect((self.carbon_server, self.carbon_port))
            for device in self.devices:
                mac = device.get('macAddress')
                data = self.do_api_call(
                    endpoint=f'devices/{mac}',
                    params={
                        "limit": 300,
                        "endDate": "2021-12-26"
                    },
                )
                for message in data:
                    timestamp = int(int(message.get('dateutc'))/1000)
                    base_metric = f'weather.{mac}'
                    self._log.info(f"Sending metrics for {mac} at timestamp {timestamp}")
                    for metric_name in message.keys():
                        try:
                            if metric_name == 'dateutc':
                                continue
                            value = message.get(metric_name)
                            if isinstance(value, (int, float)):
                                s = f'{base_metric}.{metric_name} {value} {timestamp}\n'
                                #self._log.info(f"Sending metric:")
                                #self._log.info(s)
                                sock.send(s.encode())
                        except Exception as e:
                            self._log.exception(e) 
        except Exception as e:
            self._log.exception(e)
        finally:
            sock.close()


if __name__ == '__main__':
    aw2graphite = Aw2Graphite()
    #aw2graphite.get_devices()
    aw2graphite.insert_data()
    
