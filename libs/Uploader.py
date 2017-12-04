import datetime
import ftplib
import logging
import os
import time
from glob import glob
from collections import deque
from threading import Thread, Event
import pysftp
from dateutil import zoneinfo
from .CryptUtil import SSHManager
from .SysUtil import SysUtil
import paho.mqtt.client as client
import json
from zlib import crc32
try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass

timezone = zoneinfo.get_zonefile_instance().get("Australia/Canberra")

class Uploader(Thread):
    """ Uploader class,
        used to upload,
    """
    # upload interval
    upload_interval = 120
    remove_source_files = True

    def __init__(self, identifier: str, config: dict = None, queue: deque = None):
        """
        Uploader init.
        `config` may be specified as a dict.
        
        :param identifier: 
        :param config: 
        :param queue: 
        """
        # same thread name hackery that the Camera threads use
        super().__init__(name="UPLOAD|{}".format(identifier))
        print("Thread started {}: {}".format(self.__class__, identifier))
        self.stopper = Event()
        if queue is None:
            queue = deque(tuple(), 256)

        self.communication_queue = queue
        self.identifier = identifier
        self.logger = logging.getLogger("UPLOAD|{}".format(identifier))
        self.startup_time = datetime.datetime.now()

        self.ssh_manager = SSHManager()
        self.machine_id = SysUtil.get_machineid()
        self.last_upload_time = datetime.datetime.fromtimestamp(0)
        self.last_upload_list = []
        self.total_data_uploaded_tb = 0
        self.total_data_uploaded_b = 0

        self.config = config
        if not config:
            self.config_filename = SysUtil.identifier_to_ini(self.identifier)
            self.config = SysUtil.ensure_config(self.identifier)

            self.host = self.config["ftp"]["server"]
            self.username = self.config["ftp"]["username"]
            self.password = self.config["ftp"]["password"]
            self.server_dir = self.config["ftp"]["directory"]
            self.name = self.config["camera"]["name"]
            self.source_dir = self.config["localfiles"]["upload_dir"]
            self.upload_enabled = self.config.getboolean("ftp", "enabled")
        else:
            upload_conf = self.config.get("upload", {})
            self.host = upload_conf.get("host", "sftp.traitcapture.org")
            self.username = upload_conf.get("username", "picam")
            self.password = upload_conf.get("password", "NONE_SPECIFIED")
            self.server_dir = upload_conf.get("directory", "/picam")
            self.name = self.config.get("name", self.identifier)
            self.source_dir = self.config.get("output_dir", "/home/images/{}".format(str(identifier)))
            self.upload_enabled = bool(len(upload_conf))

        self.machine_id = SysUtil.get_machineid()

        self.last_upload_list = []
        self.setupmqtt()

    def mqtt_on_message(self, client, userdata, msg):
        """
        handler for mqtt messages on a per camera basis.

        :param client: mqtt client
        :param userdata: mqtt userdata
        :param msg: message to be decoded
        """

        payload = msg.payload.decode("utf-8").strip()
        self.logger.debug("topic: {} payload: {}".format(msg.topic, payload))
        if msg.topic == "camera/{}/config".format(self.identifier):
            data = json.loads(payload)
            uploaddict = {}
            self.server_dir = data.get('server_dir', self.server_dir)
            self.username = data.get('username', self.username)
            self.password = data.get('password', self.password)
            self.host = data.get('server', self.host)
            self.source_dir = data.get("output_dir", self.source_dir)
            for k, v in data.items():
                if hasattr(self, k) and not callable(getattr(self, k)):
                    setattr(self, k, v)

    def mqtt_on_connect(self, client, *args):
        self.mqtt.subscribe("camera/{}/config".format(self.identifier), qos=1)

    def setupmqtt(self):
        client_id = str(crc32(bytes(self.identifier+"-Uploader", 'utf8')))
        self.mqtt = client.Client(client_id=client_id,
                                  clean_session=True,
                                  protocol=client.MQTTv311,
                                  transport="tcp")

        self.mqtt.on_message = self.mqtt_on_message
        self.mqtt.on_connect = self.mqtt_on_connect
        try:
            with open("mqttpassword") as f:
                self.mqtt.username_pw_set(username=self.getName(),
                                          password=f.read().strip())
        except FileNotFoundError:
            auth = self.ssh_manager.sign_message_PSS(datetime.datetime.now().replace(tzinfo=timezone).isoformat())
            if not auth:
                raise ValueError
            self.mqtt.username_pw_set(username=SysUtil.get_machineid(),
                                      password=auth)
        except:
            self.mqtt.username_pw_set(username=self.getName(),
                                      password="INVALIDPASSWORD")

        self.mqtt.connect_async("10.8.0.1", port=1883)
        self.mqtt.loop_start()

    def updatemqtt(self, msg: bytes):
        self.logger.debug("Updating mqtt")
        # update mqtt
        msg = self.mqtt.publish(payload=msg,
                                topic="camera/{}/upload".format(self.identifier),
                                qos=1)
        time.sleep(0.5)
        if not msg.is_published():
            self.mqtt.loop_stop()
            self.mqtt.loop_start()

    def upload(self, file_names):
        """
        uploads files via sftp.
        deletes the files as they are uploaded, creates new directories if needed.

        :param file_names: filenames to upload
        """
        try:
            # open link and create directory if for some reason it doesnt exist
            params = dict(host=self.host, username=self.username)
            params['cnopts'] = pysftp.CnOpts(knownhosts=self.ssh_manager.known_hosts_path)
            params['cnopts'].hostkeys = None


            if os.path.exists(self.ssh_manager.priv_path) and os.path.exists(self.ssh_manager.known_hosts_path):
                params['private_key'] = self.ssh_manager.priv_path
                params['cnopts'] = pysftp.CnOpts(knownhosts=self.ssh_manager.known_hosts_path)
            elif self.password is not None:
                params['password'] = self.password

            with pysftp.Connection(**params) as link:
                root = os.path.join(link.getcwd() or "", self.server_dir, self.name)
                root = root[1:] if root.startswith("/") else root
                # make the root dir in case it doesnt exist.
                if not link.isdir(root):
                    self.logger.debug("Making root directory")
                    self.mkdir_recursive(link, root)
                link.chdir(root)
                root = os.path.join(link.getcwd())
                # dump ze files.
                total_time = time.time()
                total_size = 0
                failed = []
                for idx, f in enumerate(file_names):
                    try:
                        onefile_time = time.time()
                        target_file = f.replace(self.source_dir, "")
                        target_file = target_file[1:] if target_file.startswith("/") else target_file
                        dirname = os.path.dirname(target_file)
                        if os.path.isdir(f):
                            self.mkdir_recursive(link, target_file)
                            continue

                        if not link.isdir(dirname):
                            self.mkdir_recursive(link, dirname)
                        link.chdir(os.path.join(root, dirname))

                        link.put(f, os.path.basename(target_file) + ".tmp")
                        if link.exists(os.path.basename(target_file)):
                            link.remove(os.path.basename(target_file))
                        link.rename(os.path.basename(target_file) + ".tmp", os.path.basename(target_file))
                        link.chmod(os.path.basename(target_file), mode=755)
                        self.total_data_uploaded_b += os.path.getsize(f)
                        if self.remove_source_files:
                            size = os.path.getsize(f)
                            total_size += size
                            mbps = (size/(time.time() - onefile_time))/1024/1024
                            if not os.path.basename(f) == "last_image.jpg":
                                os.remove(f)
                            self.logger.debug(
                                "Uploaded file {0}/{1} through sftp and removed from local filesystem, {2:.2f}Mb/s".format(idx, len(file_names), mbps))

                        self.last_upload_time = datetime.datetime.now()
                    except Exception as e:
                        self.logger.error("sftp:{}".format(str(e)))
                        failed.append(f)
                    finally:
                        link.chdir(root)
                if not self.remove_source_files:
                    if not len(failed):
                        self.logger.debug("Uploaded {} files through sftp".format(len(file_names)))
                    else:
                        self.logger.debug("Failed uploading {} files through sftp - {}".format(len(failed), str(failed)))

                mbps = (total_size/(time.time() - total_time))/1024/1024
                self.logger.debug("Finished uploading, {0:.2f}Mb/s".format(mbps))
            if self.total_data_uploaded_b > 1000000000000:
                curr = (((self.total_data_uploaded_b / 1024) / 1024) / 1024) / 1024
                self.total_data_uploaded_b = 0
                self.total_data_uploaded_tb = curr
        except Exception as e:
            # log a warning if fail because SFTP is meant to fail to allow FTP fallback
            self.logger.error("SFTP failed: {}".format(str(e)))
            self.logger.debug("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
            try:
                self.logger.debug("Connecting ftp")
                # open link and create directory if for some reason it doesnt exist
                ftp = ftplib.FTP(self.host)
                ftp.login(self.username, self.password)
                self.mkdir_recursive(ftp, os.path.join(self.server_dir, self.name))
                # dump ze files.
                for f in file_names:
                    ftp.storbinary('stor ' + os.path.basename(f), open(f, 'rb'), 1024)
                    if not os.path.basename(f) == "last_image.jpg":
                        os.remove(f)
                    self.logger.debug("Successfuly uploaded {} through ftp and removed from local filesystem".format(f))
                    self.last_upload_time = datetime.datetime.now()
            except Exception as e:
                # log error if cant upload using FTP. FTP is last resort.
                self.logger.error(str(e))

    def mkdir_recursive(self, link, remote_directory, mkdir=None, chdir=None):
        """
        Creates directories recursively on the remote server

        :param link: ftp/sftp connection object
        :param remote_directory:
        :param chdir: method used to change to a directory
        :param mkdir: method used to make a directory
        """
        if not (mkdir and chdir):
            if isinstance(link, pysftp.Connection):
                mkdir, chdir = link.mkdir, link.chdir
            elif isinstance(link, ftplib.FTP):
                mkdir, chdir = link.mkd, link.cwd
        try:
            if remote_directory in ('', "/"):
                return
            remote_dirname, basename = os.path.split(remote_directory)
            self.mkdir_recursive(link, os.path.dirname(remote_directory), mkdir=mkdir, chdir=chdir)
            try:
                chdir(basename)
            except IOError:
                self.logger.info("Sorry, just have to make some new directories, eh. ")
                mkdir(basename)
                chdir(basename)
        except Exception as e:
            self.logger.error("something went wrong making directories... {}".format(str(e)))

    def communicate_with_updater(self):
        """
        communication member. This is meant to send some metadata to the updater thread.
        """
        if not self.communication_queue:
            return

        try:
            self.logger.debug("Collecting metadata")
            data = dict(
                identifier=self.identifier,
                uploaded=SysUtil.sizeof_fmt(self.total_data_uploaded_b),
                uploads=self.last_upload_list,
                last_upload=self.last_upload_time.isoformat()
            )
            self.communication_queue.append(data)
        except Exception as e:
            self.logger.error("inter thread communication error: {}".format(str(e)))

    def run(self):
        """
        run method.
        main loop for Uploaders.

        """
        while True and not self.stopper.is_set():
            try:
                upload_list = glob(os.path.join(self.source_dir, '**'), recursive=True)
                if len(upload_list) == 0:
                    self.logger.info("No files in upload directory")
                if (len(upload_list) > 0) and self.upload_enabled:
                    start_upload_time = time.time()
                    self.logger.info("Preparing to upload %d files" % len(upload_list))
                    try:
                        l_im = os.path.join(self.source_dir, "last_image.jpg")
                        if l_im in upload_list:
                            upload_list.insert(0, upload_list.pop(upload_list.index(l_im)))
                    except Exception as e:
                        self.logger.info(
                            "Something went wrong sorting the last image to the front of the list: {}".format(str(e)))
                    self.upload(upload_list)
                    self.communicate_with_updater()
                    try:
                        self.updatemqtt(bytes(self.last_upload_time.replace(tzinfo=timezone).isoformat(), 'utf-8'))
                    except:
                        pass
                    self.logger.info(
                        "Average upload time: {0:.2f}s".format((time.time() - start_upload_time) / len(upload_list)))
                    self.logger.info("Total upload time: {0:.2f}s".format(time.time() - start_upload_time))
            except Exception as e:
                self.logger.error("Unhandled exception in uploader run method: {}".format(str(e)))
            time.sleep(Uploader.upload_interval)

    def stop(self):
        """
        stopper method
        """
        self.stopper.set()


class GenericUploader(Uploader):
    """
    Generic uploader for uploading logs sensor data, etc.
    """
    remove_source_files = True

    def fill_me(self, dict_of_values: dict):
        """
        fills self with values from a dict.

        :param dict_of_values: dictionary of key: values
        :type dict_of_values: dict
        """
        for k, v in dict_of_values.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def __init__(self,
                 identifier: str,
                 source_dir: str = None,
                 host: str = None,
                 config: dict = None,
                 queue: deque = None):
        # same thread name hackery that the Camera threads use
        Thread.__init__(self, name=identifier + "-Uploader")
        self.stopper = Event()

        queue = queue if queue is not None else deque(tuple(), 256)

        self.communication_queue = queue
        self.identifier = identifier
        self.name = identifier
        self.source_dir = source_dir
        self.logger = logging.getLogger(self.getName())
        self.startup_time = datetime.datetime.now()
        self.ssh_manager = SSHManager()
        self.machine_id = SysUtil.get_machineid()
        self.last_upload_time = datetime.datetime.fromtimestamp(0)
        self.last_upload_list = []
        self.total_data_uploaded_tb = 0
        self.total_data_uploaded_b = 0
        self.host = host or "sftp.traitcapture.org"
        self.upload_enabled = True
        self.username = "picam"
        self.password = None
        self.server_dir = "/picam"

        if config and type(config) is dict:
            self.name = config.get("name", self.name)
            self.source_dir = config.get("output_dir", self.source_dir)
            if type(config.get("upload")) is dict:
                config = config.get("upload")
            self.fill_me(config)
            self.upload_enabled = config.get("enabled", True)

    def re_init(self):
        """
        Your config is in another castle.
        """
        self.last_upload_list = []

