import json
import logging
import logging.config
import time
import yaml
from collections import deque
from threading import Thread, Event
import requests
from schedule import Scheduler
from .CryptUtil import SSHManager
from .SysUtil import SysUtil

try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass

remote_server = "traitcapture.org"

api_endpoint = "https://traitcapture.org/api/v3/remote/by-machine/{}"

class Updater(Thread):
    def __init__(self):
        Thread.__init__(self, name="Updater")
        self.logger = logging.getLogger(self.getName())
        print("Thread started {}: {}".format(self.__class__, "Updater"))
        self.communication_queue = deque(tuple(), 512)
        self.scheduler = Scheduler()
        self.scheduler.every(60).seconds.do(self.go)
        # self.scheduler.every(30).minutes.do(self.upload_log)
        self.stopper = Event()
        self.sshkey = SSHManager()
        self.identifiers = set()
        self.temp_identifiers = set()

    def upload_logs(self):
        """
        uploads rotated logs to the server.
        :return:
        """
        isonow = SysUtil.get_isonow()
        validation_msg = isonow+","+self.sshkey.sign_message(isonow)
        logs_fp = SysUtil.get_log_files()
        files = {l: open(l, 'rb') for l in logs_fp}
        a = requests.post("https://{}/raspberrypi{}/logs",
                          data={"sig_msg": isonow, "signature": validation_msg},
                          files=files)

        # clear log files if 200 returned
        if a.status_code == 200:
            SysUtil.clear_files(logs_fp)

    def add_to_identifiers(self, identifier: str):
        """
        adds an identifier to the set of identifiers.
        :param identifier: identifier to add
        :return:
        """
        self.logger.debug("Adding {} to list of permanent identifiers.".format(identifier))
        self.identifiers.add(identifier)

    def add_to_temp_identifiers(self, temp_identifier: str):
        """
        adds an identifier to the set of temporary identifiers. that may disappear
        :param temp_identifier: identifier to add
        :return:
        """
        self.logger.debug("Adding {} to list of transient identifiers.".format(temp_identifier))
        self.temp_identifiers.add(temp_identifier)

    def go(self):
        try:
            data = self.gather_data()
            data["signature"] = self.sshkey.sign_message(json.dumps(data, sort_keys=True))

            uri = api_endpoint.format(SysUtil.get_machineid())
            response = requests.patch(uri, json=data)
            # do backwards change if response is valid later.
            try:
                if response.status_code == 200:
                    # do config modify/parse of command here.
                    data = response.json()
                    for key, value in data.copy().items():
                        if value == {}:
                            del data[str(key)]

                    thed = data.pop("cameras", [])
                    data['cameras'] = {}
                    for cam in thed:
                        if cam['upload']:
                            cam['upload'] = {
                                'host': cam.pop("server"),
                                'username': cam.pop("username"),
                                'password': cam.pop("password"),
                                'server_dir': '/picam'
                            }
                        cam['output_dir'] = "/home/images/{}".format(cam['identifier'])
                        data['cameras'][cam['identifier']] = cam

                    if len(data) > 0:
                        SysUtil.write_global_config(data)
                else:
                    self.logger.error("Unable to authenticate with the server.")
            except Exception as e:
                self.logger.error("Error getting data from config/status server: {}".format(str(e)))

        except Exception as e:
            self.logger.error("Error collecting data to post to server: {}".format(str(e)))

    def set_yaml_data(self, data):
        pass

    def process_deque(self, cameras=None):
        if not cameras:
            cameras = dict()
        while len(self.communication_queue):
            item = self.communication_queue.pop()
            c = cameras.get(item['identifier'], None)
            if not c:
                cameras[item['identifier']] = item
                continue

            if item.get("last_capture", 0) > c.get("last_capture", 0):
                cameras[item['identifier']].update(item)

            if item.get("last_upload", 0) > c.get("last_upload", 0):
                cameras[item['identifier']].update(item)
        return cameras

    def gather_data(self):
        free_mb, total_mb = SysUtil.get_fs_space_mb()
        onion_address, cookie_auth, cookie_client = SysUtil.get_tor_host()

        # cameras = SysUtil.configs_from_identifiers(self.identifiers | self.temp_identifiers)
        self.logger.debug("Announcing for {}".format(str(list(self.identifiers | self.temp_identifiers))))
        conf = yaml.load(open("{}.yml".format(SysUtil.get_hostname()))) or dict()
        cameras = conf.get("cameras", dict())

        camera_data = dict(
            meta=dict(
                version=SysUtil.get_version(),
                machine=SysUtil.get_machineid(),
                internal_ip=SysUtil.get_internal_ip(),
                external_ip=SysUtil.get_external_ip(),
                hostname=SysUtil.get_hostname(),
                onion_address=onion_address,
                client_cookie=cookie_auth,
                onion_cookie_client=cookie_client,
                free_space_mb=free_mb,
                total_space_mb=total_mb
            ),
            cameras=self.process_deque(cameras=cameras),
        )
        return camera_data

    def stop(self):
        self.stopper.set()

    def run(self):
        while True and not self.stopper.is_set():
            self.scheduler.run_pending()
            time.sleep(1)
