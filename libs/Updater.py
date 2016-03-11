__author__ = 'Gareth Dunstone'
import datetime
import http.client
import json
import os
import socket
import subprocess
import time
import ssl
from configparser import ConfigParser
from glob import glob
from threading import Thread, Event
from urllib import request, parse
import logging

from schedule import Scheduler

from .AESCipher import AESCipher

hostname = "traitcapture.org"
# hostname = "localhost:5000"

class Updater(Thread):
    def __init__(self):
        Thread.__init__(self, name="Updater")

        self.scheduler = Scheduler()
        self.scheduler.every(60).seconds.do(self.go)
        self.scheduler.every(30).minutes.do(self.upload_log)
        self.logger = logging.getLogger(self.getName())
        self.stopper = Event()

    def post_multipart(self, host, selector, fields, files):
        """
        httplib form encoding. more black magic.
        only does https, no http.
        suck it, actually does https requests
        :param selector:
        :param fields:
        :param files:
        :return:
        """
        content_type, body = self.encode_multipart_formdata(fields, files)
        # Choose between http and https connections
        h = http.client.HTTPSConnection(host)
        h.putrequest('POST', selector)
        h.putheader('content-type', content_type)
        h.putheader('content-length', str(len(body)))
        h.endheaders()
        h.send(body)
        response = h.getresponse()
        return response.read()

    def encode_multipart_formdata(self, fields, files):
        """
        Black magic that does multipart form encoding.
        :param fields:
        :param files:
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

    @staticmethod
    def get_cipher():
        rpiconfig = ConfigParser()
        rpiconfig.read("picam.ini")
        return AESCipher(rpiconfig["ftp"]['pass'])

    def upload_log(self):
        logfile = "spc-eyepi.log"
        fl = glob('configs_byserial/*.ini')
        names = {}
        try:
            for fn in fl:
                c = ConfigParser()
                c.read(fn)
                try:
                    names[os.path.split(os.path.splitext(fn)[0])[-1]] = c['camera']['name']
                except:
                    pass

            aes_crypt = self.get_cipher()
            n = aes_crypt.encrypt(json.dumps(names)).decode('utf-8')
            with open(logfile, 'r') as f:
                encrypted_data = aes_crypt.encrypt(f.read())
                self.post_multipart("{}".format(hostname), "https://{}/api/camera/post-log".format(hostname), [("names", n)],
                                    [("file", "log", encrypted_data)])
        except Exception as e:
            print(str(e))

    def go(self):
        try:
            rpiconfig = ConfigParser()
            rpiconfig.read("picam.ini")
            jsondata = self.gather_data(rpiconfig)
            aes_crypt = self.get_cipher()
            ciphertext = aes_crypt.encrypt(json.dumps(jsondata))

            data = parse.urlencode({'data': ciphertext})
            data = data.encode('utf-8')
            req = request.Request('https://{}/api/camera/check-in'.format(hostname), data)

            # do backwards change if response is valid later.
            tries = 0
            while tries < 120:
                try:
                    handler = request.HTTPSHandler(context=ssl.SSLContext(ssl.PROTOCOL_TLSv1_2))
                    opener = request.build_opener(handler)
                    data = opener.open(req)
                    if data.getcode() == 200:
                        # do config modify/parse of command here.
                        data = json.loads(aes_crypt.decrypt(data.read().decode("utf-8")))
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
        tf = {"True": "on", "False": "off", "true": "on", "false": "off", True: "on", False: "off"}

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
                    try:
                        config[config_map[key][0]][config_map[key][1]] = str(value)
                    except Exception as e:
                        self.logger.error("Couldnt set item {}:{}, {}".format(key, value, str(e)))
                self.logger.info("Saving Pi config")
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
                    config[config_map[key][0]][config_map[key][1]] = str(value)
                self.writecfg(config, config_path)

    def gather_data(self, piconf):
        jsondata = {}
        version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
        jsondata["version"] = version.strip("\n")
        hn = None
        try:
            jsondata["external_ip"] = \
                json.loads(request.urlopen('https://api.ipify.org/?format=json', timeout=10).read().decode('utf-8'))[
                    'ip']
        except Exception as e:
            self.logger.error(str(e))

        with open("/etc/hostname", "r") as fn:
            hn = fn.readlines()[0]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 0))
            jsondata['internal_ip'] = s.getsockname()[0]
        except Exception as e:
            self.logger.error(str(e))

        try:
            with open("/home/tor_private/hostname") as f:
                onion_address = f.read().replace('\n', '')
            jsondata["onion_address"] = onion_address.split(" ")[0]
            jsondata["onion_cookie_auth"] = onion_address.split(" ")[1]
            jsondata["onion_cookie_client"] = onion_address.split(" ")[-1]
        except Exception as e:
            self.logger.error(str(e))

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

        if os.path.isfile("webcam.ini"):
            configs["webcam"] = ConfigParser()
            configs["webcam"].read("webcam.ini")

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
            self.logger.warning("Using picam as key in json metadata {}".format(str(e)))

        return jsondata

    def stop(self):
        self.stopper.set()

    def run(self):
        while True and not self.stopper.is_set():
            self.scheduler.run_pending()
            time.sleep(1)
