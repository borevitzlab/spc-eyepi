import subprocess, sys, platform
import datetime, time
import eyepi
import pysftp as sftp
import logging, logging.config
from mounting import *
from ConfigParser import SafeConfigParser
from optparse import OptionParser

# Global configuration variables
camera_name = platform.node()
hostname = ""
user = ""
passwd = ""
timebetweenshots = 0
config_filename = 'eyepi.ini'
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".CR2"

logging.config.fileConfig(config_filename)
logger = logging.getLogger(__name__)

# Configuration variables
config = SafeConfigParser()

def setup(dump_values = False):
    """ Setup Global configuration variables
    """

    global config, config_filename
    global camera_name, hostname, user, passwd, timebetweenshots

    config.read(config_filename)

    camera_name = config.get("camera","name")
    hostname = config.get("ftp","server")
    user = config.get("ftp","user")
    passwd = config.get("ftp","pass")
    timebetweenshots = config.getint("timelapse","duration")

    if (dump_values):
        print(camera_name)
        print(hostname)
        print(user)
        print(passwd)
        print(timebetweenshots)
        sys.exit(0)

def sftpUpload(filename):
   """ Secure upload the image file to the Server
   """

   global hostname, user, passwd

   try:
       logger.debug("Connecting")
       link = sftp.Connection(host=hostname, username=user, password=passwd)

       logger.debug("Uploading")
       link.put(filename)

       logger.debug("Disconnecting")
       link.close()

       logger.info("Successfuly uploaded %s" % filename)

   except Exception, e:
       logger.error(str(e))

def timestamp():
    """ Build a timestamp in the required format
    """
    st = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d_%H-%M-%S')
    return st

def timestamped_imagename():
    """ Build the pathname for a captured image.
    """
    global camera_name

    return camera_name + '_' + timestamp() + default_extension


if __name__ == "__main__":

    usage = "usage: %prog [options] arg"
    parser = OptionParser(usage)

    (options, args) = parser.parse_args()

    setup()

    C = eyepi.camera()

    camera_fs_unmount()

    # Check command line arguments
    if (len(args)>0) and (args[0].lower() == "info"):
         # Display information on the camera
         print C.abilities
         sys.exit(0)

    if (len(args)>0) and (args[0].lower() == "once"):
        # Override the operating times
        timestartfrom = datetime.time.min
        timestopat = datetime.time.max

    ok = True

    while (ok):

        if (datetime.datetime.now().time() > timestartfrom) and (datetime.datetime.now().time() < timestopat):

            # The time now is within the operating times
            logger.debug("Capturing Image")

            image_file = timestamped_imagename()

            C.capture_image(image_file)

            sftpUpload(image_file)

        # If the user has specified 'once' then we can stop now
        if (len(args)>0) and (args[0].lower() == "once"):
            break

        for s in range(0,timebetweenshots):
            print(timebetweenshots-s);
            time.sleep(1)
