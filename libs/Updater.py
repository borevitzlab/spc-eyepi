__author__ = 'Gareth Dunstone'
import datetime
import json
import os
import socket
import subprocess
import time
from configparser import ConfigParser
from glob import glob
from threading import Thread, Event
from urllib import request, parse

from schedule import Scheduler

from .AESCipher import AESCipher


class Updater(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.scheduler = Scheduler()
        self.scheduler.every(10).seconds.do(self.go)
        self.stopper = Event()

    def go(self):
        try:
            rpiconfig = ConfigParser()
            rpiconfig.read("picam.ini")
            jsondata = self.gather_data(rpiconfig)
            aes_crypt = AESCipher(rpiconfig["ftp"]['pass'])
            ciphertext = aes_crypt.encrypt(json.dumps(jsondata))

            data = parse.urlencode({'data': ciphertext})
            data = data.encode('utf-8')
            req = request.Request('http://phenocam.org.au/checkin', data)

            # do backwards change if response is valid later.
            tries = 0
            while tries < 120:
                data = request.urlopen(req)
                if data.getcode() == 200:
                    # do config modify/parse of command here.
                    data = json.loads(aes_crypt.decrypt(data.read().decode("utf-8")))
                    for key,value in data.copy().items():
                        if value == {}:
                            del data[key]
                    if len(data) > 0:
                        self.set_configdata(data)
                    break
                time.sleep(5)
                tries += 1
        except Exception as e:
            print(str(e))

    def writecfg(self, config, filename):
        with open(filename, 'w') as configfile:
            config.write(configfile)

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
        tf = {"True": "on", "False": "off"}

        for serialnumber, setdata in data.items():
            config = ConfigParser()
            with open("/etc/machine-id") as f:
                m_id = str(f.read())
                m_id = m_id.strip('\n')
            if m_id == serialnumber:
                # modify picam file if machine id is the sn
                config_path = "picam.ini"
                config.read(config_path)
                for key, value in setdata.items():
                    if value in tf.keys():
                        value = tf[value]
                    if value in ["starttime", "stoptime"]:
                        # parse datetimes correctly, because they are gonna be messy.
                        dt = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.Z")
                        value = dt.strftime('%H:%M')
                    config[config_map[key][0]][config_map[key][1]] = value
                self.writecfg(config, config_path)

            if os.path.isfile(os.path.join("configs_byserial", serialnumber + ".ini")):
                # modify camera by serial if available, otherwise 404.
                config_path = os.path.join("configs_byserial", serialnumber + ".ini")
                config.read(config_path)
                for key, value in setdata.items():
                    if value in tf.keys():
                        value = tf[value]
                    if value in ["starttime", "stoptime"]:
                        # parse datetimes correctly, because they are gonna be messy.
                        dt = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.Z")
                        value = dt.strftime('%H:%M')
                    config[config_map[key][0]][config_map[key][1]] = value
                self.writecfg(config, config_path)


    def gather_data(self, piconf):
        jsondata = {}
        version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
        jsondata["version"] = version.strip("\n")
        hn = None
        try:
            jsondata["external_ip"] = \
            json.loads(request.urlopen('https://api.ipify.org/?format=json', timeout=10).read().decode('utf-8'))['ip']
        except Exception as e:
            print(str(e))

        with open("/etc/hostname", "r") as fn:
            hn = fn.readlines()[0]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 0))
            jsondata['internal_ip'] = s.getsockname()[0]
        except:
            print(str(e))

        metadatas = {}
        metadatas_from_cameras_fn = glob("*.json")
        for fn in metadatas_from_cameras_fn:
            with open(fn, 'r') as f:
                metadatas[os.path.splitext(fn)[0]] = json.loads(f.read())

        jsondata['metadata'] = metadatas

        a_statvfs = os.statvfs("/")
        free_space = a_statvfs.f_frsize * a_statvfs.f_bavail
        total_space = a_statvfs.f_frsize * a_statvfs.f_blocks
        for x in range(0, 2):
            free_space /= 1024.0
            total_space /= 1024.0
        jsondata['free_space_mb'] = free_space
        jsondata['total_space_mb'] = total_space
        jsondata["name"] = hn

        piconf = ConfigParser()
        piconf.read("picam.ini")
        configs = {}
        for file in glob(os.path.join("configs_byserial", "*.ini")):
            configs[os.path.basename(file)[:-4]] = ConfigParser()
            configs[os.path.basename(file)[:-4]].read(file)
        jsondata['cameras'] = {}
        for serial, cam_config in configs.items():
            conf = {}
            for section in cam_config.sections():
                if not section == "formatter_logfileformatter" and not section == "formatter_simpleFormatter":
                    conf[section] = dict(cam_config.items(section))
            jsondata['cameras'][serial] = conf
        rpc = {}
        for section in piconf.sections():
            if not section == "formatter_logfileformatter" and not section == "formatter_simpleFormatter":
                rpc[section] = dict(piconf.items(section))
        try:
            with open("/etc/machine-id") as f:
                ser = str(f.read())
                ser = ser.strip('\n')
                jsondata['cameras'][ser] = rpc
                jsondata['metadata'][ser] = jsondata['metadata']['picam']
                del jsondata['metadata']['picam']
        except Exception as e:
            jsondata['cameras']['picam'] = rpc
            jsondata['cameras']['picam']['error'] = str(e)

        return jsondata

    def stop(self):
        self.stopper.set()

    def run(self):
        while True and not self.stopper.is_set():
            self.scheduler.run_pending()
            time.sleep(1)
