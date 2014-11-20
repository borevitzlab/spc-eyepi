#!/usr/bin/python
import os, subprocess, sys, platform
import datetime, time, shutil

import pysftp as sftp
import logging, logging.config
import shutil
from glob import glob
from mounting import *
from ConfigParser import SafeConfigParser
from optparse import OptionParser
#import eyepi
# Global configuration variables
camera_name = platform.node()
timebetweenshots = 0
config_filename = 'eyepi.ini'
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".CR2"
imagedir = "images"
copydir = "copying"
convertcmdline1 = "dcraw -q 0 -w -H 5 -b 8 %s"
convertcmdline2 = "convert %s %s"
convertcmdline3 = "convert %s -resize 800x600 %s"
filetypes = ["JPG", "jpeg","jpg","CR2","RAW","NEF"]


# We can't start if no config file
if not os.path.exists(config_filename):
    print("The configuration file %s was not found in the current directory. Try copying the one in the sample directory to %s"
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

    global config, config_filename, imagedir, copydir
    global camera_name, timebetweenshots
    global timestartfrom, timestopat, convertcmdline1, convertcmdline2

    config.read(config_filename)

    camera_name = config.get("camera","name")
    timebetweenshots = config.getint("timelapse","interval")
    imagedir = config.get("images","directory","images")
    copydir = config.get("copying","directory","copying")
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

    return os.path.join(imagedir, camera_name + '_' + timestamp(tn) + default_extension)

def convertCR2Jpeg(filename):
    """
    Convert a .CR2 file to jpeg
    """

    global convertcmdline1, convertcmdline2

    try:

        logger.debug("Converting .CR2 Image")

        raw_filename = filename
        ppm_filename = filename[:-4] + '.ppm'
        jpeg_filename = filename[:-4] + '.jpg'

        # Here we convert from .cr2 to .ppm
        cmd1 = os.system(convertcmdline1 % raw_filename)
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
        
        cmd3 = convercmdline3 % (ppm_filename, os.path.join("static", "dslr_last_image.jpg"))
        cmdresults = subprocess.check_output(cmd3.split(' '))
        if smdresults.lower().find('error')!=-1:
            logger.error(cmdresults)
        elif len(cmdresults)!=0:
            logger.debug(cmdresults)

        os.remove(ppm_filename)

    except Exception, e:
        logger.error("Image file Converion error - %s" % str(e))
        logger.error("filename: %s" % filename)

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
             print(C.abilities)
             sys.exit(0)

        if (len(args)>0) and (args[0].lower() == "once"):
            # Override the operating times
            timestartfrom = datetime.time.min
            timestopat = datetime.time.max

        ok = True
        c = None
        next_capture = datetime.datetime.now()

        while (ok):
            
            if c == None:
                try:
                    a = 0
                    # Camera object not yet initialised
                    #camera_fs_unmount()
                    #c = eyepi.camera()
                except Exception, e:
                    if (tn > timestartfrom) and (tn < timestopat):
                        logger.error("Camera not connected/powered - " + str(e))
                    else:
                        logger.debug("Camera not connected/powered - " + str(e))
                        
            tn = datetime.datetime.now()
            
            if (tn>=next_capture) and (tn.time() > timestartfrom) and (tn.time() < timestopat) and (config.get("camera","enabled")=="on"):
                try:
                     # The time now is within the operating times
                    logger.info("Capturing Image")
                    if next_capture:
                        logger.info("time now %s" % tn.isoformat())
                        logger.info("this capture at - %s" % next_capture.isoformat())
                    next_capture += datetime.timedelta(seconds = timebetweenshots)
                    logger.info("next capture at - %s" % next_capture.isoformat())
                    raw_image = timestamped_imagename(tn)
                    jpeg_image = timestamped_imagename(tn)[:-4]+".jpg"
                    #No conversion needed, just take 2 files, 1 jpeg and 1 raw
                    # using this way of capturing is more risky than just calling "gphoto --capture-and-download"

                    #subprocess.call(["gphoto2 --set-config capturetarget=sdram --capture-image-and-download --filename "+raw_image+"%C"] , shell=True)
                    cmd = ["gphoto2 --set-config capturetarget=sdram --capture-image --wait-event-and-download=13s --filename='"+os.path.join(imagedir, os.path.splitext(raw_image)[0])+".%C'"]
                    subprocess.call(cmd, shell=True)
                    logger.info("Capture Complete")
                    logger.info("Moving and renaming image files, buddy");
                    files = glob(os.path.join(imagedir,'*.jpg'))
                    for filetype in filetypes:
                        files.extend(glob(os.path.join(imagedir,"*."+filetype)))

                    for file in files:
                        ext = os.path.splitext(file)[-1].lower()
                        name = os.path.splitext(raw_image)[0]
                        if ext == ".jpeg" or ".jpg":
                            shutil.copy(file,os.path.join("static", "dslr_last_image.jpg"))
                            if config.get("ftp","uploadwebcam") == "on":
                                shutil.copy(file,os.path.join(copydir, "dslr_last_image.jpg"))
                        if config.get("ftp","uploadtimestamped")=="on":
                            logger.info("saving timestamped image for you, buddy")
                            os.rename(file,os.path.join(copydir, os.path.basename(name+ext)))
                        else:
                            logger.info("deleting file")
                            os.remove(file)
                        logger.info("Captured and stored - %s" % os.path.basename(name+ext))
                        
                    # image resizing, too processor intensive to do on a pi
                    #try:
                    #   os.system(convertcmdline3 % (jpeg_image, os.path.join("static", "dslr_last_imag$
                    #except Exception as e:
                    #    logger.error("Sorry, I had an error: %s" % str(e))

                    logger.info("Waiting until next capture at %s" % next_capture.isoformat())

                except Exception, e:
                    logger.error("Image Capture error - " + str(e))
                    c = None

            # If the user has specified 'once' then we can stop now
            if (len(args)>0) and (args[0].lower() == "once"):
                break

            # Delay between shots
            if next_capture.time() < timestopat:
                logger.debug("Next capture at %s" % next_capture.isoformat())
            else:
                logger.info("Capture will stop at %s" % timestopat.isoformat())


    except KeyboardInterrupt:
        sys.exit(0)
