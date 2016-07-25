import datetime
import http.client
import json
import logging
import os
import socket
import ssl
import subprocess
import time
from configparser import ConfigParser
from glob import glob

from collections import deque
from threading import Thread, Event
from urllib import request, parse
from schedule import Scheduler
from .CryptUtil import SSHManager
from .SysUtil import SysUtil

remote_server = "traitcapture.org"

# hostname = "localhost:5000"

def encode_multipart_formdata(fields, files):
    """
    Black magic that does multipart form encoding.
    :param fields:
    :param files: iterable of tuple with (key, filename, file as bytes
    :return:
    """
    BOUNDARY_STR = '----------ThIs_Is_tHe_bouNdaRY_$'
    CRLF = bytes("\r\n", "ASCII")
    L = []
    for (key, value) in fields:
        L.append(bytes("--" + BOUNDARY_STR, "ASCII"))
        L.append(bytes('Content-Disposition: form-data; name="{}"'.format(key), "ASCII"))
        L.append(b'')
        L.append(bytes(value, "ASCII"))
    for (key, filename, value) in files:
        L.append(bytes('--' + BOUNDARY_STR, "ASCII"))
        L.append(bytes('Content-Disposition: form-data; name="{}"; filename="{}"'.format(key, filename), "ASCII"))
        L.append(bytes('Content-Type: application/octet-stream', "ASCII"))
        L.append(b'')
        L.append(value)
    L.append(bytes('--' + BOUNDARY_STR + '--', "ASCII"))
    L.append(b'')
    body = CRLF.join(L)
    content_type = 'multipart/form-data; boundary=' + BOUNDARY_STR
    return content_type, body


class Updater(Thread):
    def __init__(self):
        Thread.__init__(self, name="Updater")
        self._communication_queue = deque(tuple(), 512)
        self.scheduler = Scheduler()
        self.scheduler.every(60).seconds.do(self.go)
        # self.scheduler.every(30).minutes.do(self.upload_log)
        self.logger = logging.getLogger(self.getName())
        self.stopper = Event()
        self.sshkey = SSHManager()

    @property
    def communication_queue(self):
        return self._communication_queue

    def post_multipart(self, host, selector, fields, files):
        """
        httplib form encoding. more black magic.
        only does https, no http.
        suck it, actually does https requests
        todo: work out how to sign this.
        :param selector:
        :param fields:
        :param files:
        :return:
        """
        content_type, body = encode_multipart_formdata(fields, files)
        # Choose between http and https connections
        h = http.client.HTTPSConnection(host)
        h.putrequest('POST', selector)
        h.putheader('content-type', content_type)
        h.putheader('content-length', str(len(body)))
        h.endheaders()
        h.send(body)
        response = h.getresponse()
        return response.read()

    def upload_logs(self):
        """
        this will soon use
        :return:
        """
        pass

    def go(self):
        try:
            data = self.gather_data()
            data["signature"] = self.sshkey.sign_message(json.dumps(data, sort_keys=True))
            req = request.Request('https://{}/api/camera/check-in/{}'.format(remote_server,
                                                                             SysUtil.get_machineid()),
                                  bytes(json.dumps(data),"utf-8"))
            req.add_header('Content-Type', 'application/json')

            # do backwards change if response is valid later.
            tries = 0
            while tries < 120:
                try:
                    handler = request.HTTPSHandler(context=ssl.SSLContext(ssl.PROTOCOL_TLSv1_2))
                    opener = request.build_opener(handler)
                    data = opener.open(req)
                    if data.getcode() == 200:
                        # do config modify/parse of command here.
                        data = json.loads(data.read().decode("utf-8"))
                        for key, value in data.copy().items():
                            if value == {}:
                                del data[str(key)]
                        if len(data) > 0:
                            self.set_config_data(data)
                        break
                except Exception as e:
                    self.logger.error("Error getting the data {}".format(str(e)))
                time.sleep(5)
                tries += 1

        except Exception as e:
            print(str(e))

    def set_config_data(self, data):
        for identifier, update_data in data.items():
            # dont rewrite empty...
            if not len(update_data):
                continue

            config = SysUtil.ensure_config(identifier)
            sections = set(config.sections()).intersection(set(update_data.keys()))
            for section in sections:
                update_section = update_data[section]
                options = set(config.options(section)).intersection(set(update_section.keys()))
                for option in options:
                    config.set(section, option, str(update_section[option]))

            SysUtil.write_config(config, identifier)

    def get_config_data(self, data):
        for identifier, misc_data in data.items():
            config = SysUtil.ensure_config(identifier)
            for section in config.sections():
                if not type(misc_data.get(section, None)) is dict:
                    misc_data[section] = dict()

                for option in config.options(section):
                    misc_data[section][option] = config.get(section, option)
        return data

    def process_deque(self):
        cameras = dict()
        while len(self._communication_queue):
            item = self._communication_queue.pop()
            c = cameras.get(item['identifier'], None)
            if not c:
                cameras[item['identifier']] = item
                continue

            if item.get("last_capture", datetime.datetime.min) > c.get("last_capture", datetime.datetime.min):
                cameras[item['identifier']].update(item)

            if item.get("last_upload", datetime.datetime.min) > c.get("last_upload", datetime.datetime.min):
                cameras[item['identifier']].update(item)
        return cameras

    def gather_data(self):
        free_mb, total_mb = SysUtil.get_fs_space_mb()
        onion_address, cookie_auth, cookie_client = SysUtil.get_tor_host()

        camera_data = dict(
            meta=dict(
                version=SysUtil.get_version(),
                machine=SysUtil.get_machineid(),
                internal_ip=SysUtil.get_internal_ip(),
                external_ip=SysUtil.get_internal_ip(),
                hostname=SysUtil.get_hostname(),
                onion_address=onion_address,
                client_cookie=cookie_auth,
                onion_cookie_client=cookie_client,
                free_space_mb=free_mb,
                total_space_mb=total_mb
            ),
            cameras=self.get_config_data(self.process_deque())
        )
        return camera_data

    def stop(self):
        self.stopper.set()

    def run(self):
        while True and not self.stopper.is_set():
            self.scheduler.run_pending()
            time.sleep(1)
