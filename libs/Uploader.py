import datetime
import ftplib
import io
import json
import logging
import os
import socket
import subprocess
import time
from configparser import ConfigParser
from glob import glob
from socket import socket, SOCK_DGRAM, AF_INET
from threading import Thread, Event
import paramiko
import pysftp
from .CryptUtil import SSHManager
from .SysUtil import SysUtil

def pysftp_connection_init_patch(self, host, username=None, private_key=None, port=22):
    """
    monkeypatch for pysftp to support paramiko.RSAkey
    :param self:
    :param host:
    :param username:
    :param private_key:
    :param port:
    :return:
    """
    self._sftp_live = False
    self._sftp = None
    self._transport_live = False
    try:
        self._transport = paramiko.Transport((host, port))
        self._transport_live = True
    except (AttributeError, socket.gaierror):
        raise pysftp.ConnectionException(host, port)
    self._transport.connect(username=username, pkey=private_key)


class Uploader(Thread):
    """ Uploader class,
        used to upload,
    """

    def __init__(self, config_filename, name=None):
        # same thread name hackery that the Camera threads use
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)
        self.stopper = Event()
        # and the same setup stuff that they use as well.

        self.config_filename = config_filename
        self.last_config_modify_time = os.stat(config_filename).st_mtime
        self.machine_id_last = os.stat("/etc/machine-id").st_mtime

        self.logger = logging.getLogger(self.getName())
        self.startup_time = datetime.datetime.now()
        self.total_data_uploaded_tb = 0
        self.total_data_uploaded_b = 0
        # these things are to none now so we can check for None later.
        self.last_upload_time = None

        self.setup()

    def get_sn(self, file):
        """
        returns either the serialnumber
        :param file: filename
        :param machine_id: the current machine id.
        :return:
        """
        fsn = next(iter(os.path.splitext(os.path.basename(file))), "")
        return "".join((x if idx > len(fsn) - 1 else fsn[idx] for idx, x in enumerate(self.machine_id)))

    def setup(self):
        """
        setup to be run each time the config is reloaded
        :return:
        """

        # TODO: move the timeinterval to the config file and get it from there, this _should_ avoid too many requests to the sftp server.
        self.machine_id = SysUtil.get_machineid()
        self.serialnumber = SysUtil.get_serialnumber_from_filename(self.config_filename, self.machine_id)
        self.json_path = SysUtil.serialnumber_to_json(self.serialnumber, self.machine_id)

        self.timeinterval = 60
        self.config = ConfigParser()
        self.config.read(self.config_filename)
        self.hostname = self.config["ftp"]["server"]
        self.user = self.config["ftp"]["user"]
        self.passwd = self.config["ftp"]["pass"]
        self.target_directory = self.config["ftp"]["directory"]
        self.cameraname = self.config["camera"]["name"]
        self.upload_directory = self.config["localfiles"]["upload_dir"]
        self.last_upload_list = []
        self.ssh_manager = SSHManager()
        self.last_capture_time = datetime.datetime.fromtimestamp(0)

    def sendMetadataSFTP(self, datas):
        """
        sets the metadata on the server.
        :param datas:
        :return:
        """
        try:
            # self.logger.debug("trying to store new ip on server using SFTP, friend!")
            # create new link and create directory if it doesnt exist already
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory, self.cameraname))
            # open a file and write the html snippet.
            for name, data in list(datas.items()):
                f = link.open(os.path.join(self.target_directory, self.cameraname, name), mode='w')
                f.write(data)
                f.close()
        except Exception as e:
            # this is going to trigger if the user provided cannot log into SFTP (ie they give an ftp user/pass
            self.logger.warning("SFTP:  " + str(e))
            return False
        return True

    def sendMetadataFTP(self, datas):
        """ Stores IP address on server using FTP
            *datas is a dictionary of metadata files as datas[fname]=data
        """
        try:
            # self.logger.debug("trying to store metadata on server using FTP, friend!")
            # similar to the SFTP
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user, self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory, self.cameraname))
            for name, data in list(datas.items()):
                # some sorcery that I dont understand:
                unicodestring = str(data)
                assert isinstance(unicodestring, str)
                # I think this makes it into a file-like object?
                file = io.BytesIO(unicodestring.encode("utf-8"))
                # upload it
                ftp.storbinary('STOR ' + name, file)
            ftp.quit()
        except Exception as e:
            # if FTP fails log an error not a warning like SFTP
            self.logger.error(str(e))
            return False
        return True

    def sftp_upload(self, filenames):
        """
        uploads files via sftp. deletes the files as they are uploaded.
        :param filenames: filenames to upload
        :return:
        """
        try:
            self.logger.debug("Connecting sftp and uploading buddy")
            # open link and create directory if for some reason it doesnt exist
            params = dict(host=self.hostname, username=self.user)
            if self.ssh_manager.ssh_agentKey:
                params['private_key'] = self.ssh_manager.ssh_agentKey
                pysftp.Connection.__init__ = pysftp_connection_init_patch
            else:
                params['password'] = self.passwd
            with pysftp.Connection(self.hostname, **params) as link:
                link.chdir("/")
                self.mkdir_p_sftp(link, os.path.join(self.target_directory, self.cameraname))
                self.logger.debug("Uploading")
                # dump ze files.
                for f in filenames:
                    # use sftpuloadtracker to handle the progress
                    try:
                        link.put(f, os.path.basename(f) + ".tmp")
                        if link.exists(os.path.basename(f)):
                            link.remove(os.path.basename(f))
                        link.rename(os.path.basename(f) + ".tmp", os.path.basename(f))
                        link.chmod(os.path.basename(f), mode=775)
                        self.total_data_uploaded_b += os.path.getsize(f)
                        os.remove(f)
                        self.logger.debug("Successfuly uploaded %s through sftp and removed from local filesystem" % f)
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
            self.logger.error("SFTP: {}".format(str(e)))
            return False
        return True

    def ftp_upload(self, filenames):
        """
        uploads via ftp
        :param filenames:
        :return:
        """
        self.logger.info("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
        try:
            self.logger.debug("Connecting ftp")
            # open link and create directory if for some reason it doesnt exist
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user, self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory, self.cameraname))
            self.logger.info("Uploading")
            # dump ze files.
            for f in filenames:
                totalSize = os.path.getsize(f)
                ftp.storbinary('stor ' + os.path.basename(f), open(f, 'rb'), 1024)
                self.logger.debug("Successfuly uploaded %s through ftp and removed from local filesystem" % f)
                os.remove(f)
        except Exception as e:
            # log error if cant upload using FTP. FTP is last resort.
            self.logger.error(str(e))
            return False
        return True

    def mkdir_p_sftp(self, sftp, remote_directory):
        """
        creates directories recursively on the remote server
        :param sftp: sftp handler
        :param remote_directory:
        :return:
        """
        try:
            if remote_directory == '/':
                sftp.chdir('/')
                return
            if remote_directory == '':
                return
            remote_dirname, basename = os.path.split(remote_directory)
            self.mkdir_p_sftp(sftp, os.path.dirname(remote_directory))
            try:
                sftp.chdir(basename)
            except IOError:
                self.logger.info("Sorry, just have to make some new directories, eh. (sftp)")
                sftp.mkdir(basename)
                sftp.chdir(basename)
        except Exception as e:
            self.logger.error("something went wrong making directories... {}".format(str(e)))

    def mkdir_p_ftp(self, ftp, remote_directory):
        """
        creates directories recursively on the remote server
        warning: black magic ahead
        :param ftp: ftp handler
        :param remote_directory:
        :return:
        """
        if remote_directory == '/':
            ftp.cwd('/')
            return
        if remote_directory == '':
            return
        remote_dirname, basename = os.path.split(remote_directory)
        self.mkdir_p_ftp(ftp, os.path.dirname(remote_directory))
        try:
            ftp.cwd(basename)
        except ftplib.error_perm as e:
            self.logger.info("Sorry, just have to make some new directories, eh. (ftp)")
            ftp.mkd(basename)
            ftp.cwd(basename)

    def set_metadata_on_server(self, list_of_uploads):
        """
        collects metadata from various parts of the pi and sets the data on the server.
        :param list_of_uploads:
        :return:
        """
        try:
            data = {}
            # data entries must be strings so just serialise a dict
            self.serialnumber = SysUtil.get_serialnumber_from_filename(self.config_filename, self.machine_id)
            SysUtil.serialnumber_to_json(self.serialnumber, self.machine_id)
            self.logger.debug("Collecting metadata")

            try:
                with open(SysUtil.serialnumber_to_json(self.serialnumber, self.machine_id), 'r') as f:
                    jsondata = json.load(f)
            except Exception as e:
                self.logger.debug("Couldn't load json rewriting... EXC: {}".format(str(e)))
                with open(SysUtil.serialnumber_to_json(self.serialnumber, self.machine_id),'w') as f:
                    f.write("{}")

            try:
                jsondata["uploaded"] = SysUtil.sizeof_fmt(self.total_data_uploaded_b)
                jsondata["list_of_uploads"] = list_of_uploads
                jsondata['last_upload_time'] = 0
                # need to check against none because otherwise it gets stuck in a broken loop.
                if self.last_upload_time is not None:
                    try:
                        jsondata["last_upload_time"] = (datetime.datetime.now()-datetime.datetime.fromtimestamp(0)).total_seconds() - time.daylight*3600
                    except:
                        pass

                jsondata['last_upload_time_human'] = datetime.datetime.fromtimestamp(jsondata['last_upload_time']).isoformat()
            except Exception as e:
                self.logger.error("Couldnt collect metadata: %s" % str(e))
            self.logger.debug("Sending metadata to server now")
            with open(SysUtil.get_serialnumber_from_filename(self.config_filename), 'w') as f:
                f.write(json.dumps(jsondata, indent=4, separators=(',', ': '), sort_keys=True))
            if not self.sendMetadataSFTP(data):
                self.sendMetadataFTP(data)
        except Exception as e:
            self.logger.error(str(e))

    def run(self):
        """ Main upload loop
        """
        while True and not self.stopper.is_set():
            # sleep for a while
            time.sleep(self.timeinterval)
            # check and see if config has changed.
            if os.stat(self.config_filename).st_mtime != self.last_config_modify_time or os.stat(
                    "/etc/machine-id").st_mtime != self.machine_id_last:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                self.machine_id_last = os.stat("/etc/machine-id").st_mtime
                self.setup()

            try:
                upload_list = glob(os.path.join(self.upload_directory, '*'))
                if len(upload_list) == 0:
                    self.logger.info("No files in upload directory")
                if (len(upload_list) > 0) and self.config.getboolean("ftp", "enabled"):
                    self.logger.info("Preparing to upload %d files" % len(upload_list))
                    try:
                        l_im = os.path.join(self.upload_directory, "last_image.jpg")
                        if l_im in upload_list:
                                upload_list.insert(0, upload_list.pop(upload_list.index(l_im)))
                    except Exception as e:
                        self.logger.info("Something went wrong sorting the last image to the front: {}".format(str(e)))

                    if not self.sftp_upload(upload_list):
                        self.ftp_upload(upload_list)
                    self.last_upload_time = datetime.datetime.now()
                try:
                    if not upload_list == []:
                        self.last_upload_list = upload_list
                    self.set_metadata_on_server(self.last_upload_list)
                except Exception as e:
                    self.logger.error(str(e))
            except Exception as e:
                self.logger.error("ERROR: UPLOAD %s" % str(e))

    def stop(self):
        self.stopper.set()
