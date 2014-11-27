#!/usr/bin/python
import os, subprocess, sys, platform
import datetime, time, shutil
import shutil
import pysftp as sftp
import logging, logging.config
from mounting import *
from ConfigParser import SafeConfigParser
from optparse import OptionParser

# Global configuration variables
camera_name = platform.node()
timebetweenshots = 0
config_filename = 'picam.ini'
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".jpg"
imagedir = "images"
copydir = "copying"

# We can't start if no config file
if not os.path.exists(config_filename):
    print("The configuration file %s was not found in the current directory. \nTry copying the one in the sample directory to %s"
           % (config_filename,config_filename))
    sys.exit(1)

# Logging setup
logging.config.fileConfig(config_filename)
logger = logging.getLogger('picamcap')

# Configuration variables
config = SafeConfigParser()

def setup(dump_values = False):
    """ Setup Global configuration variables
    """

    global config, config_filename, imagedir, copydir
    global camera_name, timebetweenshots
    global timestartfrom, timestopat, convertcmdline1, convertcmdline2

    config.read(config_filename)

    camera_name = config.get("camera","name")
    timebetweenshots = config.getint("timelapse","interval")
    imagedir = config.get("images","directory","images")
    copydir = config.get("copying","directory","copying")

    try:
        tval = config.get('timelapse','starttime')
        if len(tval)==5:
            if tval[2]==':':
                timestartfrom = datetime.time(int(tval[:2]),int(tval[3:]))
                logger.debug("Starting at %s" % timestartfrom.isoformat())
    except Exception, e:
        logger.error("Time conversion error startime - %s" % str(e))

    try:
        tval = config.get('timelapse','stoptime')
        if len(tval)==5:
            if tval[2]==':':
                timestopat = datetime.time(int(tval[:2]),int(tval[3:]))
                logger.debug("Stopping at %s" % timestopat.isoformat())
    except Exception, e:
        logger.error("Time conversion error stoptime - %s" % str(e))

    if not os.path.exists(imagedir):
        # All images stored in their own seperate directory
        logger.info("Creating Image Storage directory %s" % imagedir)
        os.makedirs(imagedir)
    else:
        for the_file in os.listdir(imagedir):
            file_path =os.path.join(imagedir, the_file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                    logger.debug("Deleting previous file in the spool eh, Sorry.")
            except Exception, e:
                logger.error("Sorry, buddy! Couldn't delete the files in spool, eh! Error: %s" % e)


    if not os.path.exists(copydir):
        logger.info("creating copyfrom dir %s" % copydir)
        os.makedirs(copydir)
    else:
        for the_file in os.listdir(copydir):
            file_path =os.path.join(copydir, the_file)
            try:
                if os.path.isfile(file_path):
                    #os.unlink(file_path)
                    logger.debug("Deleting previous file ready for copy, Sorry.")
            except Exception, e:
                logger.error("Sorry, buddy! Couldn't delete the files ready for copy, eh! Error: %s, eh." % e)

    if (dump_values):
        # For debugging, we can dump some configuration values
        logger.debug(camera_name)
        logger.debug(timebetweenshots)
        sys.exit(0)

def timestamp(tn):
    """ Build a timestamp in the required format
    """
    st = tn.strftime('%Y_%m_%d_%H_%M_%S')
    return st

def timestamped_imagename(timen):
    """ Build the pathname for a captured image.
    """
    global camera_name, imagedir
    return os.path.join(imagedir, camera_name + '_' + timestamp(timen) + default_extension)

if __name__ == "__main__":

    usage = "usage: %prog [options] arg"
    parser = OptionParser(usage)

    try:
        (options, args) = parser.parse_args()

        setup()

        # Check command line arguments
        if (len(args)>0) and (args[0].lower() == "info"):
             # Display information on the camera
             print(C.abilities)
             sys.exit(0)

        if (len(args)>0) and (args[0].lower() == "once"):
            # Override the operating times
            timestartfrom = datetime.time.min
            timestopat = datetime.time.max

        ok = True
        next_capture = datetime.datetime.now()
        while (ok):

            tn = datetime.datetime.now()
            birthday = datetime.datetime(1990, 07,17,12,12,12,13)
            if tn-next_capture > datetime.timedelta(seconds = timebetweenshots*4):
                next_capture=tn+datetime.timedelta(seconds=timebetweenshots)
            if tn<birthday:
                logger.info("my creator hasnt been born yet")
                
            if (tn>birthday) and (tn>=next_capture) and (tn.time() > timestartfrom) and (tn.time() < timestopat) and (config.get("camera","enabled")=="on"):

                next_capture += datetime.timedelta(seconds = timebetweenshots)

                try:

                    # The time now is within the operating times
                    logger.info("Capturing Image")

                    image_file = timestamped_imagename(tn)
                    
                    os.system("raspistill --nopreview -o "+image_file)
                    logger.info("Copying the image to the web service, buddy") 
                    shutil.copy(image_file,os.path.join("static","temp","pi_last_image.jpg"))
               
                    if config.get("ftp","uploadwebcam") == "on":
                        shutil.copy(image_file,os.path.join(copydir, "pi_last_image.jpg"))
                    if config.get("ftp","uploadtimestamped")=="on":
                        logger.info("saving timestamped image for you, buddy")
                        os.rename(image_file ,os.path.join(copydir,os.path.basename(image_file))) 
                    else:
                        logger.info("deleting file")
                        os.remove(file)
                    logger.info("Image Captured and stored - %s" % os.path.basename(image_file))
                    # Delay between shots
                    if next_capture.time() < timestopat:
                        logger.debug("Next capture at %s" % next_capture.isoformat())
                   else:
                        logger.info("Capture will stop at %s" % timestopat.isoformat())
                except Exception, e:
                    logger.error("Image Capture error - " + str(e))
                    c = None

            # If the user has specified 'once' then we can stop now
            if (len(args)>0) and (args[0].lower() == "once"):
                break

            
            time.sleep(0.01)

    except KeyboardInterrupt:
        sys.exit(0)

