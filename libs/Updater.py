__author__ = 'Gareth Dunstone'
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
from threading import Thread, Event
from urllib import request, parse
from schedule import Scheduler
from .CryptUtil import SSHManager
from ..libs import SysUtil


hostname = "traitcapture.org"


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
        self.scheduler = Scheduler()
        self.scheduler.every(60).seconds.do(self.go)
        self.scheduler.every(50).minutes.do(self.set_self_data)
        # self.scheduler.every(30).minutes.do(self.upload_log)
        self.logger = logging.getLogger(self.getName())
        self.stopper = Event()
        self.sshkey = SSHManager()
        self.version = self.hostname = self.internal_ip = self.machine_id = self.onion_address = \
            self.onion_cookie_auth = self.onion_cookie_client = self.free_space = self.total_space = None

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
            data = parse.urlencode(self.gather_data())
            data = data.encode('utf-8')
            req = request.Request('https://{}/api/camera/{}/check-in'.format(hostname, self.hostname), data)
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
                            self.set_configdata(data)
                        break
                except Exception as e:
                    self.logger.error("Error getting the data {}".format(str(e)))
                time.sleep(5)
                tries += 1

        except Exception as e:
            print(str(e))


    def set_configdata(self, data):
        config_map = {
            'name': ('camera', 'name'),
            'camera_enabled': ('camera', 'enabled'),
            'upload_enabled': ('ftp', 'uploaderenabled'),
            'upload_username': ('ftp', 'user'),
            'upload_pass': ('ftp', 'pass'),
            'upload_server': ('ftp', 'server'),
            'upload_timestamped': ('ftp', 'uploadtimestamped'),
            'upload_webcam': ('ftp', 'uploadwebcam'),
            'interval_in_seconds': ('timelapse', 'interval'),
            'starttime': ('timelapse', 'starttime'),
            'stoptime': ('timelapse', 'stoptime')
        }
        tf = {"True": "on", "False": "off", "true": "on", "false": "off", True: "on", False: "off"}

        for serialnumber, setdata in data.items():
            config = ConfigParser()
            config_path = SysUtil.serialnumber_to_ini(serialnumber, machine_id=self.machine_id)
            if not len(config.read(config_path)):
                continue

            for key, value in setdata.items():
                if value in tf.keys():
                    value = tf[value]
                if value in ["starttime", "stoptime"]:
                    # parse datetimes correctly, because they are gonna be messy.
                    dt = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.Z")
                    value = dt.strftime('%H:%M')
                config[config_map[key][0]][config_map[key][1]] = str(value)
            with open(config_path, 'w') as configfile:
                config.write(configfile)

    def get_camera_data(self, serialnumber):
        config_path = SysUtil.serialnumber_to_ini(serialnumber, machine_id=self.machine_id)
        json_path = SysUtil.serialnumber_to_json(serialnumber,machine_id=self.machine_id)
        cfg = ConfigParser()
        cfg.read(config_path)
        d = dict((s, dict(cfg.items(s))) for s in cfg.sections())
        d.update(json.loads(json_path))
        return d

    def set_self_data(self):
        self.version = SysUtil.get_version()
        self.internal_ip = SysUtil.get_internal_ip()
        self.external_ip = SysUtil.get_external_ip()
        self.onion_address, self.onion_cookie_auth, self.onion_cookie_client = SysUtil.get_tor_host()
        self.free_space, self.total_space = SysUtil.get_fs_space_mb()

    def gather_data(self):
        jsondata = dict(
            meta=dict(
                version=self.version,
                machine_id=self.machine_id,
                internal_ip=self.internal_ip,
                external_ip=self.external_ip,
                hostname=self.hostname,
                onion_address=self.onion_address,
                onion_cookie_auth=self.onion_cookie_auth,
                onion_cookie_client=self.onion_cookie_client,
                free_space_mb=self.free_space,
                total_space_mb=self.total_space
            ),
            cameras=dict((SysUtil.get_serialnumber_from_filename(fn, self.machine_id),
                          self.get_camera_data(SysUtil.get_serialnumber_from_filename(fn, self.machine_id)))
                         for fn in glob("*.json"))
        )

        return jsondata

    def stop(self):
        self.stopper.set()

    def run(self):
        while True and not self.stopper.is_set():
            self.scheduler.run_pending()
            time.sleep(1)
