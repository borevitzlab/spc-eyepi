import datetime
import ftplib
import logging
import os
import time
from glob import glob
from collections import deque
from threading import Thread, Event
import pysftp
from .CryptUtil import SSHManager
from .SysUtil import SysUtil

logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)


#
# def pysftp_connection_init_patch(self, host, username=None, private_key=None, port=22):
#     self._sftp_live = False
#     self._sftp = None
#     self._transport_live = False
#     try:
#         self._transport = paramiko.Transport((host, port))
#         self._transport_live = True
#     except (AttributeError, gaierror):
#         raise pysftp.ConnectionException(host, port)
#     self._transport.connect(username=username, pkey=private_key)


class Uploader(Thread):
    """ Uploader class,
        used to upload,
    """
    # upload interval
    upload_interval = 120
    remove_source_files = True

    def __init__(self, identifier: str, queue: deque = None, config_filename: str = None):
        # same thread name hackery that the Camera threads use
        Thread.__init__(self, name=identifier + "-Uploader")
        self.stopper = Event()
        if queue is None:
            queue = deque(tuple(), 256)
        self.communication_queue = queue
        self.identifier = identifier
        self.logger = logging.getLogger(self.getName())
        self.startup_time = datetime.datetime.now()

        self.config_filename = config_filename or SysUtil.identifier_to_ini(self.identifier)

        self.ssh_manager = SSHManager()
        self.machine_id = SysUtil.get_machineid()
        self.last_upload_time = datetime.datetime.fromtimestamp(0)
        self.last_upload_list = []

        self.total_data_uploaded_tb = \
            self.total_data_uploaded_b = 0

        self.config = \
            self.hostname = \
            self.username = \
            self.password = \
            self.target_directory = \
            self.camera_name = \
            self.upload_directory = \
            self.upload_enabled = None

        self.re_init()
        SysUtil().add_watch(self.config_filename, self.re_init)

    def re_init(self):
        """
        setup to be run each time the config is reloaded
        :return:
        """
        self.machine_id = SysUtil.get_machineid()
        if os.path.splitext(self.config_filename)[-1] == ".ini":
            self.config = SysUtil.ensure_config(self.identifier)
            self.hostname = self.config["ftp"]["server"]
            self.username = self.config["ftp"]["username"]
            self.password = self.config["ftp"]["password"]
            self.target_directory = self.config["ftp"]["directory"]
            self.camera_name = self.config["camera"]["name"]
            self.upload_directory = self.config["localfiles"]["upload_dir"]
            self.upload_enabled = self.config.getboolean("ftp", "enabled")
        elif os.path.splitext(self.config_filename)[-1] == ".yml":
            self.config = SysUtil.open_yaml(self.config_filename)
            self.hostname = self.config['server']
            self.username = self.config['username']
            self.password = self.config['password']
            self.upload_directory = self.config['upload_dir']
            self.target_directory = self.config['server_dir']
            self.camera_name = self.config['name']
            self.upload_enabled = True
        self.last_upload_list = []

    def upload(self, file_names):
        """
        uploads files via sftp. deletes the files as they are uploaded.
        :param file_names: filenames to upload
        :return:
        """
        try:
            self.logger.debug("Connecting sftp and uploading buddy")
            # open link and create directory if for some reason it doesnt exist
            params = dict(host=self.hostname, username=self.username)
            params['cnopts'] = pysftp.CnOpts(knownhosts='/home/.ssh/known_hosts')
            params['cnopts'].hostkeys = None

            if os.path.exists("/home/.ssh/id_rsa") and os.path.exists('/home/.ssh/known_hosts'):
                params['private_key'] = "/home/.ssh/id_rsa"
                params['cnopts'] = pysftp.CnOpts(knownhosts='/home/.ssh/known_hosts')
            else:
                params['password'] = self.password

            with pysftp.Connection(**params) as link:
                self.mkdir_recursive(link, os.path.join(self.target_directory, self.camera_name))
                self.logger.debug("Uploading...")
                # dump ze files.
                for f in file_names:
                    # use sftpuloadtracker to handle the progress
                    try:
                        link.put(f, os.path.basename(f) + ".tmp")
                        if link.exists(os.path.basename(f)):
                            link.remove(os.path.basename(f))
                        link.rename(os.path.basename(f) + ".tmp", os.path.basename(f))
                        link.chmod(os.path.basename(f), mode=755)
                        self.total_data_uploaded_b += os.path.getsize(f)
                        if self.remove_source_files:
                            os.remove(f)
                            self.logger.debug("Successfully uploaded {} through sftp and removed from local filesystem".format(f))
                        else:
                            self.logger.debug("Successfully uploaded {} through sftp".format(f))

                        self.last_upload_time = datetime.datetime.now()
                    except Exception as e:
                        self.logger.error("sftp:{}".format(str(e)))
                self.logger.debug("Disconnecting, eh")
            if self.total_data_uploaded_b > 1000000000000:
                curr = (((self.total_data_uploaded_b / 1024) / 1024) / 1024) / 1024
                self.total_data_uploaded_b = 0
                self.total_data_uploaded_tb = curr
        except Exception as e:
            # log a warning if fail because SFTP is meant to fail to allow FTP fallback
            self.logger.error("SFTP failed: {}".format(str(e)))
            self.logger.info("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
            try:
                self.logger.debug("Connecting ftp")
                # open link and create directory if for some reason it doesnt exist
                ftp = ftplib.FTP(self.hostname)
                ftp.login(self.username, self.password)
                self.mkdir_recursive(ftp, os.path.join(self.target_directory, self.camera_name))
                self.logger.info("Uploading")
                # dump ze files.
                for f in file_names:
                    ftp.storbinary('stor ' + os.path.basename(f), open(f, 'rb'), 1024)
                    os.remove(f)
                    self.logger.debug("Successfuly uploaded %s through ftp and removed from local filesystem" % f)
                    self.last_upload_time = datetime.datetime.now()
            except Exception as e:
                # log error if cant upload using FTP. FTP is last resort.
                self.logger.error(str(e))

    def mkdir_recursive(self, link, remote_directory, mkdir=None, chdir=None):
        """
        creates directories recursively on the remote server

        :param link: ftp/sftp connection object
        :param remote_directory:
        :param chdir:
        :param mkdir:
        :return:
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
        :return:
        """
        if not self.communication_queue:
            return

        try:
            self.logger.debug("Collecting metadata")
            data = dict(
                identifier=self.identifier,
                uploaded=SysUtil.sizeof_fmt(self.total_data_uploaded_b),
                uploads=self.last_upload_list,
                last_upload=int(self.last_upload_time.strftime("%s"))
            )
            self.communication_queue.append(data)
        except Exception as e:
            self.logger.error("inter thread communication error: {}".format(str(e)))

    def run(self):
        """
        run method.
        main loop for Uploaders.
        :return:
        """
        while True and not self.stopper.is_set():
            try:
                upload_list = glob(os.path.join(self.upload_directory, '*'))
                if len(upload_list) == 0:
                    self.logger.info("No files in upload directory")
                if (len(upload_list) > 0) and self.upload_enabled:
                    start_upload_time = time.time()
                    self.logger.info("Preparing to upload %d files" % len(upload_list))
                    try:
                        l_im = os.path.join(self.upload_directory, "last_image.jpg")
                        if l_im in upload_list:
                            upload_list.insert(0, upload_list.pop(upload_list.index(l_im)))
                    except Exception as e:
                        self.logger.info("Something went wrong sorting the last image to the front of the list: {}".format(str(e)))
                    self.upload(upload_list)
                    self.communicate_with_updater()
                    self.logger.info(
                        "Average upload time: {0:.2f}s".format((time.time() - start_upload_time) / len(upload_list)))
                    self.logger.info("Total upload time: {0:.2f}s".format(time.time() - start_upload_time))
            except Exception as e:
                self.logger.error("Unhandled exception in uploader run method: {}".format(str(e)))
            time.sleep(Uploader.upload_interval)

    def stop(self):
        """
        stopper method
        :return:
        """
        self.stopper.set()


class GenericUploader(Uploader):
    """
    generic uploader for uploading logs sensor data, etc.
    """
    remove_source_files = False

    def __init__(self, identifier: str, source_dir: str, hostname: str, queue: deque = None):
        # same thread name hackery that the Camera threads use
        Thread.__init__(self, name=identifier + "-Uploader")
        self.stopper = Event()
        if queue is None:
            queue = deque(tuple(), 256)
        self.communication_queue = queue
        self.identifier = identifier
        self.camera_name = identifier
        self.upload_directory = source_dir
        self.logger = logging.getLogger(self.getName())
        self.startup_time = datetime.datetime.now()
        self.ssh_manager = SSHManager()
        self.machine_id = SysUtil.get_machineid()
        self.last_upload_time = datetime.datetime.fromtimestamp(0)
        self.last_upload_list = []
        self.total_data_uploaded_tb = 0
        self.total_data_uploaded_b = 0
        self.hostname = hostname
        self.upload_enabled = True
        self.username = "INTENTIONALLY BLANK"
        self.password = "INTENTIONALLY BLANK"
        self.target_directory = "/"

    def re_init(self):
        """
        there is no config to be reloaded here...
        :return:
        """
        self.last_upload_list = []
