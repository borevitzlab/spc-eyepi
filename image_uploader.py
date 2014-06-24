import subprocess, os, sys, platform
import datetime, time, glob
import eyepi
import pysftp as sftp
import logging, logging.config
from mounting import *
from ConfigParser import SafeConfigParser
from optparse import OptionParser

# Global configuration variables
hostname = ""
user = ""
passwd = ""
config_filename = 'eyepi.ini'
imagedir = "images"
timeinterval = 30

# We can't start if no config file
if not os.path.exists(config_filename):
    print("The configuration file %s was not found in the current directory. \nTry copying the one in the sample directory to %s"
           % (config_filename,config_filename)) 
    sys.exit(1)

# Logging setup
logging.config.fileConfig(config_filename)
logger = logging.getLogger(__name__)

# Configuration variables
config = SafeConfigParser()

def setup(dump_values = False):
    """ Setup Global configuration variables
    """

    global config, config_filename, imagedir
    global camera_name, hostname, user, passwd, timebetweenshots

    config.read(config_filename)

    hostname = config.get("ftp","server")
    user = config.get("ftp","user")
    passwd = config.get("ftp","pass")
    imagedir = config.get("images","directory","images")

def sftpUpload(filenames):
   """ Secure upload the image file to the Server
   """

   global hostname, user, passwd

   try:
       logger.debug("Connecting")
       link = sftp.Connection(host=hostname, username=user, password=passwd)

       logger.debug("Uploading")
       for f in filenames:
           link.put(f)
           os.remove(f)

       logger.debug("Disconnecting")
       link.close()

       logger.info("Successfuly uploaded %s" % filename)

   except Exception, e:
       logger.error(str(e))
       return str(e)
       
   return None
  

if __name__ == "__main__":

    usage = "usage: %prog [options] arg"
    parser = OptionParser(usage)

    (options, args) = parser.parse_args()

    setup()

    ok = True

    while (ok):
        
        try:

            upload_list = glob.glob(os.path.join(imagedir,'*'))
            logger.debug("Preparing to upload %d files" % len(upload_list))
          
            r = sftpUpload(upload_list)

            time.sleep(timeinterval)

        except Exception, e:
           logger.error(str(e))
