#!/usr/bin/python
import os, subprocess, sys, platform
import datetime, time, shutil
import eyepi
import pysftp as sftp
import logging, logging.config
from mounting import *
from ConfigParser import SafeConfigParser
from optparse import OptionParser

# Global configuration variables
camera_name = platform.node()
timebetweenshots = 0
config_filename = 'eyepi.ini'
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".CR2"
imagedir = "images"
convertcmdline1 = "dcraw -q 0 -w -H 5 -b 8 %s"
convertcmdline2 = "convert %s %s"

# We can't start if no config file
if not os.path.exists(config_filename):
    print("The configuration file %s was not found in the current directory. \nTry copying the one in the sample directory to %s"
           % (config_filename,config_filename)) 
    sys.exit(1)

# Logging setup
logging.config.fileConfig(config_filename)
logger = logging.getLogger('timedcapture')

# Configuration variables
config = SafeConfigParser()

def setup(dump_values = False):
    """ Setup Global configuration variables
    """

    global config, config_filename, imagedir
    global camera_name, timebetweenshots
    global timestartfrom, timestopat, convertcmdline1, convertcmdline2

    config.read(config_filename)

    camera_name = config.get("camera","name")
    timebetweenshots = config.getint("timelapse","interval")
    imagedir = config.get("images","directory","images")
    if config.has_option("convertor","commandline"):
        convertcmdline = config.get("convertor","commandline")
    
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

    if (dump_values):
        # For debugging, we can dump some configuration values
        logger.debug(camera_name)
        logger.debug(timebetweenshots)
        sys.exit(0)

def timestamp():
    """ Build a timestamp in the required format
    """
    st = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d_%H-%M-%S')
    return st

def timestamped_imagename():
    """ Build the pathname for a captured image.
    """
    global camera_name, imagedir

    return os.path.join(imagedir, camera_name + '_' + timestamp() + default_extension)

def convertCR2Jpeg(filename):
    """
    Convert a .CR2 file to jpeg
    """

    global convertcmdline1, convertcmdline2
    
    try:    
        raw_filename = filename
        ppm_filename = filename[:-4] + '.ppm'
        jpeg_filename = filename[:-4] + '.jpg'
    
        # Here we convert from .cr2 to .ppm
        cmd1 =  convertcmdline1 % raw_filename
        cmdresults = subprocess.check_output(cmd1.split(' '))
        if cmdresults.lower().find('error:')!=-1:
            logger.error(cmdresults)
        elif len(cmdresults)!=0:
            logger.debug(cmdresults)
    
        # Next we convert from ppm to jpeg
        cmd2 = convertcmdline2 % (ppm_filename,jpeg_filename)
        cmdresults = subprocess.check_output(cmd2.split(' '))
        if cmdresults.lower().find('error:')!=-1:
            logger.error(cmdresults)
        elif len(cmdresults)!=0:
            logger.debug(cmdresults)

        os.remove(ppm_filename)
        
    except Exception, e:
        logger.error("Image file Converion error - %s" % str(e))
    
    return ([raw_filename,jpeg_filename])

if __name__ == "__main__":

    usage = "usage: %prog [options] arg"
    parser = OptionParser(usage)

    try:
        (options, args) = parser.parse_args()

        setup()

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
        c = None
        next_capture = None
        
        while (ok):

            tn = datetime.datetime.now().time()

            if c == None:
                try:
                    # Camera object not yet initialised
                    camera_fs_unmount()
                    c = eyepi.camera()
                except Exception, e:
                    if (tn > timestartfrom) and (tn < timestopat):
                        logger.error("Camera not connected/powered - " + str(e))
                    else:
                        logger.debug("Camera not connected/powered - " + str(e))

            if (tn > timestartfrom) and (tn < timestopat):

                next_capture = datetime.datetime.now() + datetime.timedelta(seconds = timebetweenshots)

                try:

                    # The time now is within the operating times
                    logger.debug("Capturing Image")

                    image_file = timestamped_imagename()

                    c.capture_image(image_file)

                    converted_files = convertCR2Jpeg(image_file)

                    logger.info("Image Captured and stored - %s" % os.path.basename(image_file))
                    
                    # Save the jpeg to the web servers directory
                    for i in converted_files:
                        if i.endswith('.jpg'):
                            shutil.copy_file(i,os.path.join('static',os.path.basename(i)))

                except Exception, e:
                    logger.error("Image Capture error - " + str(e))
                    c = None
                    
            else:
                print('.')
                time.sleep(30)
                continue

            # If the user has specified 'once' then we can stop now
            if (len(args)>0) and (args[0].lower() == "once"):
                break

            # Delay between shots
            while datetime.datetime.now() < next_capture:
                time.sleep(1)

    except KeyboardInterrupt:
        sys.exit(0)
        
