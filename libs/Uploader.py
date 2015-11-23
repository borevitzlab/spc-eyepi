__author__ = 'Gareth Dunstone'
import datetime
import ftplib
import io
import json
import logging
import os
import subprocess
import time
from configparser import ConfigParser
from glob import glob
from socket import socket, SOCK_DGRAM, AF_INET
from threading import Thread, Event

import pysftp


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
        self.last_config_modify_time = None
        self.config_filename = config_filename
        self.logger = logging.getLogger(self.getName())
        self.startup_time = datetime.datetime.now()
        self.total_data_uploaded_tb = 0
        self.total_data_uploaded_b = 0
        self.setup()

    def setup(self):
        # TODO: move the timeinterval to the config file and get it from there, this _should_ avoid too many requests to the sftp server.
        self.timeinterval = 10
        self.config = ConfigParser()
        self.config.read(self.config_filename)
        self.hostname = self.config["ftp"]["server"]
        self.user = self.config["ftp"]["user"]
        self.passwd = self.config["ftp"]["pass"]
        self.target_directory = self.config["ftp"]["directory"]
        self.cameraname = self.config["camera"]["name"]
        self.upload_directory = self.config["localfiles"]["upload_dir"]
        self.last_upload_list = []
        self.last_capture_time = datetime.datetime.fromtimestamp(0)
        # these things are to none now so we can check for None later.
        self.last_upload_time = None
        self.ipaddress = None

    def sendMetadataSFTP(self, datas):
        """ Stores IP address on server using SecureFTP
            *datas is a dictionary of metadata files as datas[fname]=data
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

    def sftpUpload(self, filenames):
        """ Secure upload the image file to the Server
        """
        try:
            self.logger.debug("Connecting sftp and uploading buddy")
            # open link and create directory if for some reason it doesnt exist
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
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
                except Exception as e:
                    self.logger.warning("sftp:%s" % str(e))
            self.logger.debug("Disconnecting, eh")
            link.close()
            if self.total_data_uploaded_b > 1000000000000:
                curr = (((self.total_data_uploaded_b / 1024) / 1024) / 1024) / 1024
                self.total_data_uploaded_b = 0
                self.total_data_uploaded_tb = curr

        except Exception as e:
            # log a warning if fail because SFTP is meant to fail to allow FTP fallback
            self.logger.warning("SFTP:  " + str(e))
            return False
        return True

    def ftpUpload(self, filenames):
        """ insecure upload for backwards compatibility
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
        """ Recursive directory sorcery for SecureFTP
        """
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

    def mkdir_p_ftp(self, ftp, remote_directory):
        """ Recursive directory sorcery for FTP
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
        """ Metadata collector
        """

        def sizeof_fmt(num, suffix='B'):
            for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
                if abs(num) < 1024.0:
                    return "%3.1f%s%s" % (num, unit, suffix)
                num /= 1024.0
            return "%.1f%s%s" % (num, 'Yi', suffix)

        try:
            data = {}
            # data entries must be strings so just serialise
            # some more sorcery that i dont fully understand. Connects to googles DNS server
            s = socket(AF_INET, SOCK_DGRAM)
            s.connect(("8.8.8.8", 0))
            self.ipaddress = s.getsockname()[0]
            onion_address = ""
            self.logger.debug("Collecting metadata")
            jsondata = json.load(self.config_filename[:-4].split("/")[-1]+".json")
            try:
                with open("/home/tor_private/hostname") as f:
                    onion_address = f.read().replace('\n', '')
                jsondata["onion_address"] = onion_address.split(" ")[0]
                jsondata["onion_cookie_auth"] = onion_address.split(" ")[1]
                jsondata["onion_cookie_client"] = onion_address.split(" ")[-1]
            except Exception as e:
                self.logger.warning("couldnt do onion {}".format(str(e)))
            if self.last_upload_time is None:
                fullstr = "<h1>" + str(
                    self.cameraname) + "</h1><br>Havent uploaded yet<br> Ip address: " + self.ipaddress + "<br>onion_address: " + onion_address + "<br><a href='http://" + self.ipaddress + ":5000'>Config</a>"
            else:
                fullstr = "<h1>" + str(self.cameraname) + "</h1><br>Last upload at: " + self.last_upload_time.strftime(
                    "%y-%m-%d %H:%M:%S") + "<br> Ip address: " + self.ipaddress + "<br>onion_address: " + onion_address + "<br><a href='http://" + self.ipaddress + ":5000'>Config</a>"
            try:
                a_statvfs = os.statvfs("/")
                free_space = sizeof_fmt(a_statvfs.f_frsize * a_statvfs.f_bavail)
                total_space = sizeof_fmt(a_statvfs.f_frsize * a_statvfs.f_blocks)
                jsondata["name"] = self.cameraname
                jsondata["tb_uploaded"] = self.total_data_uploaded_tb
                jsondata["smaller_uploaded"] = sizeof_fmt(self.total_data_uploaded_b)
                jsondata["free_space"] = free_space
                jsondata["total_space"] = total_space
                jsondata["serialnumber"] = self.config_filename[:-4].split("/")[-1]
                jsondata["ip_address"] = self.ipaddress
                jsondata["list_of_uploads"] = list_of_uploads
                jsondata["capture_limits"] = self.config['timelapse']['starttime'] + " - " + self.config['timelapse'][
                    'stoptime']

                jsondata['last_upload_time'] = 0

                # need to check against none because otherwise it gets stuck in a broken loop.
                if self.last_upload_time is not None:
                    try:
                        jsondata["last_upload_time"] = time.time() - 3600
                    except:
                        pass

                jsondata['last_upload_time_human'] = datetime.datetime.fromtimestamp(jsondata['last_upload_time']).isoformat()
                jsondata["version"] = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
            except Exception as e:
                self.logger.error("Couldnt collect metadata: %s" % str(e))
            data["metadata.json"] = json.dumps(jsondata, indent=4, separators=(',', ': '), sort_keys=True)
            data["ipaddress.html"] = fullstr
            self.logger.debug("Sending metadata to server now")
            with open(str(jsondata['serialnumber']) + ".json", 'w') as f:
                f.write(data['metadata.json'])
            if not self.sendMetadataSFTP(data):
                self.sendMetadataFTP(data)
        except Exception as e:
            self.logger.error(str(e))
            time.sleep(5)

    def run(self):
        """ Main upload loop
        """
        while True and not self.stopper.is_set():
            # sleep for a while
            time.sleep(self.timeinterval)
            # check and see if config has changed.
            if os.stat(self.config_filename).st_mtime != self.last_config_modify_time:
                # reset last change time to last and setup() again
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                self.setup()
            try:
                upload_list = glob(os.path.join(self.upload_directory, '*'))
                if (len(upload_list) == 0):
                    self.logger.debug("No files in upload directory")
                if (len(upload_list) > 0) and self.config["ftp"]["uploaderenabled"] == "on":
                    self.logger.info("Preparing to upload %d files" % len(upload_list))
                    if not self.sftpUpload(upload_list):
                        self.ftpUpload(upload_list)
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
