#!/usr/bin/python
from __future__ import division
import subprocess, os, sys, platform
import datetime, time, glob
import pysftp
import ftplib
import logging, logging.config
import io
from socket import socket, SOCK_DGRAM, AF_INET 
from mounting import *
from ConfigParser import SafeConfigParser
from optparse import OptionParser


# Global configuration variables
config_filename = 'picam.ini'
imagedir = "picamimages"
timeinterval = 1
uploadtimedelay = 1

# We can't start if no config file
if not os.path.exists(config_filename):
    print("The configuration file %s was not found in the current directory. \nTry copying the one in the sample directory to %s"
           % (config_filename,config_filename)) 
    sys.exit(1)

# Logging setup
logging.config.fileConfig(config_filename)
logger = logging.getLogger(__name__)

def checkipaddressonserver(thisip, hostname, cameraname, uploaddir, user, passwd):
    if not getmakeserveripaddressSFTP(thisip, hostname, cameraname, uploaddir, user, passwd):
        getmakeserveripaddressFTP(thisip, hostname, cameraname, uploaddir, user, passwd)

def getmakeserveripaddressSFTP(thisip,hostname,cameraname,uploaddir,user,passwd):
    try:
        logger.debug("checking and matching ip using SFTP, eh...")
        link = pysftp.Connection(host=hostname, username=user, password=passwd)
        link.chdir("/")
        mkdir_p_sftp(link, os.path.join(uploaddir,cameraname) )
        try:
       	    serversip = link.get("ipaddress", preserve_mtime=True)
            logger.debug("IP on server %s" % serversip)
        except Exception as e:
            logger.debug("Storing new ip on server")
            f = link.open(os.path.join(uploaddir,cameraname,"ipaddress"), mode='w')
            f.write(thisip)
            f.close()
            serversip=thisip
            return serversip
        if serversip != thisip:
            logger.debug("new IP, updating the ip, eh")
            f = link.open(os.path.join(uploaddir,cameraname,"ipaddress"), mode='w')
            f.write(thisip)
            f.close()
            serversip=thisip
            return serversip	    
    except Exception as e:
        logger.error(str(e))
        return False

def getmakeserveripaddressFTP(thisip,hostname,cameraname,uploaddir,user,passwd):
    try:
        logger.debug("Checking and matching ip using FTP, eh...")
        ftp = ftplib.FTP(hostname)
        ftp.login(user,passwd)
        mkdir_p_ftp(ftp, os.path.join(uploaddir,cameraname))
        try:
            files = []
            ftp.retrlines('RETR ipaddress', files.append)
            serversip = files[0]
            logger.debug("IP on server %s" % serversip)
        except Exception as e:
            logger.debug("Storing new ip on server")
            unicodeip = unicode(ipaddress)
            assert isinstance(unicodeip, unicode)
            file = io.BytesIO(unicodeip.encode("utf-8"))
            ftp.storbinary('STOR ipaddress',file)
            serversip = thisip
            return serversip
        if serversip != thisip:
            logger.debug("new IP, updating the ip, eh")
            unicodeip = unicode(ipaddress)
            assert isinstance(unicodeip, unicode)
            file = io.BytesIO(unicodeip.encode("utf-8"))
            ftp.storbinary('STOR ipaddress',file)
            serversip = thisip
            return serversip
    except Exception as e:
        logger.error(str(e))
        return False
    return True

def sftpUpload(filenames, hostname, cameraname, uploaddir, user, passwd):
    """ Secure upload the image file to the Server
    """
    try:
        logger.debug("Connecting sftp")
        link = pysftp.Connection(host=hostname, username=user, password=passwd)
        link.chdir("/")
        mkdir_p_sftp(link, os.path.join(uploaddir,cameraname))
        logger.debug("Uploading")
        for f in filenames:
            link.put(f,os.path.basename(f), callback=sftpuploadtracker)
            os.remove(f)
            sys.stderr.write("\n")
            logger.info("Successfuly uploaded %s through sftp and removed from local filesystem" % f)
            sys.stderr.write("\n")
        logger.debug("Disconnecting, eh")
        link.close()
        
    except Exception as e:
        logger.error(str(e))
        return False
    return True
  

def ftpUpload(filenames, hostname, cameraname, uploaddir, user, passwd):
    """ insecure upload for backwards compatibility
    """
    logger.info("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
    try:
        logger.debug("Connecting ftp")
        ftp = ftplib.FTP(hostname)
        ftp.login(user,passwd)
        mkdir_p_ftp(ftp, os.path.join(uploaddir,cameraname))
        logger.debug("Uploading")                
        for f in filenames:
            totalSize = os.path.getsize(f)
            uploadTracker = FtpUploadTracker(totalSize)
            ftp.storbinary('stor '+ os.path.basename(f), open(f, 'rb'), 1024, uploadTracker.handle)
            sys.stderr.write("\n")
            logger.info("Successfuly uploaded %s through ftp and removed from local filesystem" % f)
            sys.stderr.write("\n")
            os.remove(f)
        logger.debug("Disconnecting, eh")
        ftp.quit()
    except Exception as e:
        logger.error(str(e))
    return True

def mkdir_p_sftp(sftp, remote_directory):
    if remote_directory == '/':
        sftp.chdir('/')
        return
    if remote_directory =='':
        return
    remote_dirname, basename = os.path.split(remote_directory)
    mkdir_p_sftp(sftp, os.path.dirname(remote_directory))
    try:
        sftp.chdir(basename)
    except IOError:
        logger.debug("Sorry, just have to make some new directories, eh. (sftp)")
        sftp.mkdir(basename)
        sftp.chdir(basename)

def mkdir_p_ftp(ftp, remote_directory):
    if remote_directory == '/':
        ftp.cwd('/')
        return
    if remote_directory =='':
        return
    remote_dirname, basename = os.path.split(remote_directory)
    mkdir_p_ftp(ftp, os.path.dirname(remote_directory))
    try:
        ftp.cwd(basename)
    except ftplib.error_perm as e:
        logger.debug("Sorry, just have to make some new directories, eh. (ftp)")
        ftp.mkd(basename)
        ftp.cwd(basename)
          
class FtpUploadTracker:
    sizeWritten = 0
    totalSize = 0
    lastShownPercent = 0
    multiple = 0 
   
    def __init__(self, totalSize):
        self.totalSize = totalSize
        self.multiple = round(self.totalSize / 100)
        logger.info("----------")
        logger.info("upload size: %d" % totalSize)

    def handle(self, block):
        self.sizeWritten+=1024
        if self.sizeWritten > self.multiple:
            percentage = round((self.sizeWritten / self.totalSize)*100)
            sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
            self.lastShownPercent+=self.multiple
            sys.stderr.flush()
            
def sftpuploadtracker(transferred, total):
    
    if (transferred % (total/100)):
        percentage = round((transferred / total)*100)
        sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
        sys.stderr.flush()
            
    

if __name__ == "__main__":
    usage = "usage: %prog [options] arg"

    parser = OptionParser(usage)

    (options, args) = parser.parse_args()

    logger.info("Program Startup")
    config = SafeConfigParser()
    config.read(config_filename)
    hostname = config.get("ftp","server")
    user = config.get("ftp","user")
    passwd = config.get("ftp","pass")
    uploaddir = config.get("ftp", "directory")# + config.get("camera","name")
    cameraname = config.get("camera","name")
    imagedir = config.get("copying","directory")
    while True:
        try:
            upload_list = glob.glob(os.path.join(imagedir,'*'))
            if (len(upload_list) > 0) and config.get("ftp","uploaderenabled")=="on":
                s = socket(AF_INET, SOCK_DGRAM)
                s.connect(("www.google.com",0))
                ipaddress = s.getsockname()[0]

                logger.debug("Pausing %d seconds to wait for files to be closed" % uploadtimedelay)
                time.sleep(uploadtimedelay)

                logger.debug("Preparing to upload %d files" % len(upload_list))
                if not sftpUpload(upload_list, hostname, cameraname, uploaddir, user, passwd):
                    ftpUpload(upload_list, hostname, cameraname, uploaddir, user, passwd)
                logger.debug("checking ip address on server, eh")
                checkipaddressonserver(ipaddress, hostname,cameraname,uploaddir,user,passwd)
            time.sleep(timeinterval)
        except Exception as e:
           logger.error(str(e))
    logger.info("Program Shutdown")
