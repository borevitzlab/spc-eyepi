#!/usr/bin/python
from __future__ import division
import subprocess, os, sys, platform
import datetime, time, glob
import pysftp
import ftplib
import logging, logging.config
import io
from threading import Thread
from socket import socket, SOCK_DGRAM, AF_INET 
from ConfigParser import SafeConfigParser
from optparse import OptionParser


# Global configuration variables

timeinterval = 10
uploadtimedelay = 1
config_filename = "eyepi.ini"

# We can't start if no config file

if not os.path.exists(config_filename):
    print("The configuration file %s was not found in the current directory. \nTry copying the one in the sample directory to %s"
           % (config_filename,config_filename)) 
    sys.exit(1)

# Logging setup
logging.config.fileConfig(config_filename)
logging.getLogger("paramiko").setLevel(logging.WARNING)


class uploader(Thread):
    def __init__(self, config_filename, name = None):
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)
        self.last_config_modify_time = None
        self.config_filename = config_filename
        self.logger = logging.getLogger(self.getName())
        
        self.setup()

    def setup(self):
        self.timeinterval = 10
        self.uploadtimedelay = 1
        self.config = SafeConfigParser()
        self.config.read(self.config_filename)
        self.hostname = self.config.get("ftp","server")
        self.user = self.config.get("ftp","user")
        self.passwd = self.config.get("ftp","pass")
        self.target_directory = self.config.get("ftp", "directory")
        self.cameraname = self.config.get("camera","name")
        self.upload_directory = self.config.get("localfiles","upload_dir")
        self.last_upload_time = None
        self.ipaddress = None

    def makeserveripaddressSFTP(self,thisip):
        try:
            self.logger.debug("settings using SFTP, eh...")
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory,self.cameraname) )
            self.logger.debug("Storing new ip on server")
            f = link.open(os.path.join(self.target_directory,self.cameraname,"ipaddress.html"), mode='w')
            f.write(thisip)
            f.close()
        except Exception as e:
            self.logger.error(str(e))
            f.close()
            return False
        return True

    def makeserveripaddressFTP(self,ftp,thisip):
        try:
            self.logger.debug("setting IP using FTP, eh...")
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory,self.cameraname))
            unicodeip = unicode(thisip)
            assert isinstance(unicodeip, unicode)
            file = io.BytesIO(unicodeip.encode("utf-8"))
            ftp.storbinary('STOR ipaddress.html',file)
            ftp.quit()
        except Exception as e:
            self.logger.error(str(e))
            ftp.quit()
            return False
        return True

    def sftpUpload(self,filenames):
        """ Secure upload the image file to the Server
        """
        try:
            self.logger.debug("Connecting sftp")
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory,self.cameraname))
            self.logger.debug("Uploading")
            for f in filenames:
                link.put(f,os.path.basename(f), callback=self.sftpuploadtracker)
                os.remove(f)
                sys.stderr.write("\n")
                self.logger.info("Successfuly uploaded %s through sftp and removed from local filesystem" % f)
                sys.stderr.write("\n")
            self.logger.debug("Disconnecting, eh")
            link.close()
        except Exception as e:
            self.logger.error(str(e))
            return False
        return True
      

    def ftpUpload(self, filenames):
        """ insecure upload for backwards compatibility
        """
        self.logger.info("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
        try:
            self.logger.debug("Connecting ftp")
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user,self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory,self.cameraname))
            self.logger.debug("Uploading")                
            for f in filenames:
                totalSize = os.path.getsize(f)
                uploadTracker = FtpUploadTracker(totalSize)
                ftp.storbinary('stor '+ os.path.basename(f), open(f, 'rb'), 1024, uploadTracker.handle)
                sys.stderr.write("\n")
                self.logger.info("Successfuly uploaded %s through ftp and removed from local filesystem" % f)
                sys.stderr.write("\n")
                os.remove(f)
        except Exception as e:
            self.logger.error(str(e))
            return False
        return True

    def mkdir_p_sftp(self, sftp, remote_directory):
        if remote_directory == '/':
            sftp.chdir('/')
            return
        if remote_directory =='':
            return
        remote_dirname, basename = os.path.split(remote_directory)
        self.mkdir_p_sftp(sftp, os.path.dirname(remote_directory))
        try:
            sftp.chdir(basename)
        except IOError:
            self.logger.debug("Sorry, just have to make some new directories, eh. (sftp)")
            sftp.mkdir(basename)
            sftp.chdir(basename)

    def mkdir_p_ftp(self, ftp, remote_directory):
        if remote_directory == '/':
            ftp.cwd('/')
            return
        if remote_directory =='':
            return
        remote_dirname, basename = os.path.split(remote_directory)
        self.mkdir_p_ftp(ftp, os.path.dirname(remote_directory))
        try:
            ftp.cwd(basename)
        except ftplib.error_perm as e:
            self.logger.debug("Sorry, just have to make some new directories, eh. (ftp)")
            ftp.mkd(basename)
            ftp.cwd(basename)

    def sftpuploadtracker(self,transferred, total):
        if total/100 != 0:
            if (transferred % (total/100)):
                percentage = round((transferred / total)*100)
                sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
                sys.stderr.flush()

            
    def set_ip_on_server(self, l_last_upload_time):
        try:
            s = socket(AF_INET, SOCK_DGRAM)
            s.connect(("8.8.8.8",0))
            self.ipaddress = s.getsockname()[0]
            if l_last_upload_time == None:
                fullstr = "Havent uploaded yet<br> Ip address: "+ self.ipaddress + "<br><a href='http://" + self.ipaddress + ":5000'>Config</a>" 
            else:
                fullstr = "Last upload at: " + l_last_upload_time.strftime("%y-%m-%d %H:%M:%S") + "<br> Ip address: "+ self.ipaddress + "<br><a href='http://" + self.ipaddress + ":5000'>Config</a>"
            if not self.makeserveripaddressSFTP(fullstr):
                self.makeserveripaddressFTP(fullstr)
        except Exception as e:
            self.logger.error(str(e))
            time.sleep(5)
                              

    def run(self):
        counter = 0
        a = True
        while(a):
            counter+=1
            if self.config.get("ftp","uploaderenabled")=="on":
                self.logger.info("Waiting %d secs to check directories again" % self.timeinterval)
            time.sleep(timeinterval)
            
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                self.setup()
               
            self.set_ip_on_server(self.last_upload_time)
            upload_list = glob.glob(os.path.join(self.upload_directory,'*'))
            
            if (len(upload_list)==0):
                self.logger.info("no files in upload directory")
                
            if (len(upload_list) > 0) and self.config.get("ftp","uploaderenabled")=="on":
                self.logger.debug("Pausing %d seconds to wait for files to be closed" % self.uploadtimedelay)
                time.sleep(self.uploadtimedelay)
                self.logger.debug("Preparing to upload %d files" % len(upload_list))
                if not self.sftpUpload(upload_list):
                    self.ftpUpload(upload_list)
                self.last_upload_time = datetime.datetime.now()
            
            if counter>=3:
                a = False

            
            


class FtpUploadTracker:
    sizeWritten = 0
    totalSize = 0
    lastShownPercent = 0
    multiple = 0 
   
    def __init__(self, totalSize):
        self.totalSize = totalSize
        self.multiple = round(self.totalSize / 100)
        #logger.info("----------")
        #logger.info("upload size: %d" % totalSize)

    def handle(self, block):
        self.sizeWritten+=1024
        if self.sizeWritten > self.multiple:
            percentage = round((self.sizeWritten / self.totalSize)*100)
            sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
            self.lastShownPercent+=self.multiple
            sys.stderr.flush()
            




if __name__ == "__main__":
    usage = "usage: %prog [options] arg"
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    
    upload1 = uploader("eyepi.ini", name="DSLR_image_uploader")

    upload2 = uploader("picam.ini", name="picam_image_uploader")

    
    upload1.start()
    upload2.start()

    
    upload1.join()
    upload2.join()

