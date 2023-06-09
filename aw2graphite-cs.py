#!/usr/bin/env python3

import json
import logging
import smtplib
import socket
import time
import traceback

from datetime import datetime, timezone
from flask import Flask, request

app = Flask(__name__)
config = {}
config_file = "config.json"
state = {}
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
log = logging.getLogger()
log.setLevel(logging.INFO)


def load_config(startup=False):
    log.info("Loading config now")
    last_load_ts = time.time()
    with open(config_file, 'r') as f:
        new_config = json.load(f)

    for key, new_value in new_config.items():
        if config.get(key) != new_value:
            if not startup or key == 'LOGLEVEL':
                if isinstance(new_value, dict) or \
                        isinstance(new_value, list):
                    log.info(f"Config item {key} changed from:")
                    log.info(
                        json.dumps(self.__config.get(key),indent=2)
                    )
                    log.info("to:")
                    log.info(json.dumps(new_value, indent=2))
                else:
                    log.info(
                        f"Config item {key} changed from " +
                        f"{config.get(key)} to {new_value}"
                    )
            config[key] = new_value

    new_loglevel = new_config.get('LOGLEVEL')
    if new_loglevel:
        log.setLevel(new_loglevel)
    state['last_load_ts'] = last_load_ts
    save_state()
    return config


def load_alerts():
    state = {}
    try:
        state_file = config.get('STATE_FILE')
        with open(state_file, 'r') as f:
            log.info(f"Loading state from {state_file}")
            state = json.loads(f.read())
    except FileNotFoundError as e:
        # The file isn't there yet, so go ahead and save it
        save_state()
    return state


def save_state():
    state_file = config.get(
        'STATE_FILE',
        config.get('ALERT_STATE_FILE')
    )
    with open(state_file, 'w') as f:
        log.debug(f"Writing state to {state_file}:")
        log.debug(json.dumps(state, indent=2))
        f.write(json.dumps(state, indent=2))


def update_alert(is_alerting, metric_name, alert_msg=None):
    # Check if alerting status has changed
    if state.get(metric_name) is not None and \
            state.get(metric_name) != is_alerting:
        log.debug(f"Metric {metric_name} alert state was " +
            f"{state.get(metric_name)} and is now {is_alerting}")
        if is_alerting:
            log.debug("That makes this a problem")
            subject_prefix = "[PROBLEM]"
        else:
            log.debug("That makes this a recovery")
            subject_prefix = "[RECOVERY]"
        with smtplib.SMTP(
            config.get("SMTP_SERVER"),
            config.get("SMTP_PORT")
        ) as server:
            server.sendmail(
                config.get("ALERT_FROM"),
                config.get("ALERT_TO"),
                f'Subject: {subject_prefix} {metric_name}\n\n{alert_msg}',
            )
    state[metric_name] = is_alerting


def check_if_alerting(metric_name, value):
    threshold = None
    is_alerting = False
    alert_thresholds = config.get("ALERT_THRESHOLDS")
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
    update_alert(
        is_alerting=is_alerting,
        metric_name=metric_name,
        alert_msg=f"Weather metric {metric_name} is at value {value}, " +
            f"configured threshold is {threshold}",
    )


@app.route("/")
def handle_message():
    message = request.args
    
    mac = message.get('PASSKEY')
    timestamp = int(
        datetime.strptime(
            message.get('dateutc'),
            '%Y-%m-%d %H:%M:%S'
        ).replace(
            tzinfo=timezone.utc
        ).timestamp()
    )
    base_metric = f'weather.{mac}'
    log.info(f"Sending metrics for {mac}")
    try:
        sock = socket.socket()
        sock.connect(
            (
                config.get("CARBON_SERVER"),
                config.get("CARBON_PORT")
            )
        )
        found_alert = False
        for metric_name in message.keys():
            try:
                alert = False
                value = message.get(metric_name)
                if metric_name not in ['dateutc', 'PASSKEY', 'stationtype']:
                    try:
                        value = int(value)
                    except ValueError:
                        try:
                            value = float(value)
                        except ValueError:
                            continue
                    s = f'{base_metric}.{metric_name} {value} {timestamp}\n'
                    log.debug("Sending metric:")
                    log.debug(s)
                    sock.send(s.encode())
                    check_if_alerting(metric_name, value)
            except Exception as e:
                log.exception(e)
    except Exception as e:
        log.exception(e)
    finally:
        save_state()
        sock.close()

    return json.dumps(message, indent=2)

config = load_config(startup=True)
