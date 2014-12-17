#!/usr/bin/python
import os, subprocess, sys, platform
import datetime, time, shutil
import pysftp as sftp
import logging, logging.config
import shutil
from glob import glob
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
convertcmdline1 = "dcraw -q 0 -w -H 5 -b 8 %s"
convertcmdline2 = "convert %s %s"
convertcmdline3 = "convert %s -resize 800x600 %s"
#Acceptable filtypes, DONT INCLUDE JPG!!!
filetypes = ["CR2","RAW","NEF","JPG"]


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

    global config, config_filename, spool_dir, upload_dir
    global camera_name, timebetweenshots
    global timestartfrom, timestopat, convertcmdline1, convertcmdline2

    config.read(config_filename)

    camera_name = config.get("camera","name")
    timebetweenshots = config.getint("timelapse","interval")
    spool_dir = config.get("localfiles","spooling_dir")
    upload_dir = config.get("localfiles","upload_dir")
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

    if not os.path.exists(spool_dir):
        # All images stored in their own seperate directory
        logger.info("Creating Image Storage directory %s" % spool_dir)
        os.makedirs(spool_dir)
    else:
        for the_file in os.listdir(spool_dir):
            file_path =os.path.join(spool_dir, the_file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                    logger.debug("Deleting previous file in the spool eh, Sorry.")
            except Exception, e:
                logger.error("Sorry, buddy! Couldn't delete the files in spool, eh! Error: %s" % e)

    if not os.path.exists(upload_dir):
        logger.info("creating copyfrom dir %s" % upload_dir)
        os.makedirs(upload_dir)
    #else:
    #    for the_file in os.listdir(upload_dir):
    #        file_path =os.path.join(upload_dir, the_file)
    #        try:
    #            if os.path.isfile(file_path):
    #                #os.unlink(file_path)
    #                #logger.debug("NOT Deleting previous file ready for copy, Sorry.")
    #        except Exception, e:
    #            #logger.error("Sorry, buddy! Couldn't delete the files ready for copy, eh! Error: %s, eh." % e)

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
    global camera_name, spool_dir

    return os.path.join(spool_dir, camera_name + '_' + timestamp(timen) + default_extension)

if __name__ == "__main__":
    
    #The main loop for capture 
    #TODO: Objectify camera object and incorporate picam, threading etc.
    
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
        # need to set next capture time to now for increments.
        next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified
        configmodified = None
        while (ok):

            # testing for the config modification
            if os.stat(config_filename).st_mtime!=configmodified:
                configmodified = os.stat(config_filename).st_mtime
                # Resetup()
                setup()
                logger.debug("change in config at "+ datetime.datetime.now().isoformat() +" reloading")
            
            # set a timenow
            tn = datetime.datetime.now()
            # This is used to check and see if the date is smething ridiculous.
            birthday = datetime.datetime(1990, 07,17,12,12,12,13)
            # check if the next capture period is within 4 intervals of the time now, if not set it to timenow + interval.
            if tn-next_capture > datetime.timedelta(seconds = timebetweenshots*4):
                next_capture=tn+datetime.timedelta(seconds = timebetweenshots)
            # Log if the time isn't sane yet (needs to get it from ntpdate)
            if tn<birthday:
                logger.error("my creator hasnt been born yet")
                time.sleep(60)
            # checking if enabled and other stuff
            if (tn>birthday) and (tn>=next_capture) and (tn.time() > timestartfrom) and (tn.time() < timestopat) and (config.get("camera","enabled")=="on"):
                try:
                     # The time now is within the operating times
                    logger.info("Capturing Image")
                    if next_capture:
                        logger.info("time now %s" % tn.isoformat())
                        logger.info("this capture at - %s" % next_capture.isoformat())

                    # increment next_capture by increment
                    # this causes less drift than adding increment to datetime.datetime.now() 
                    next_capture += datetime.timedelta(seconds = timebetweenshots)

                    # setting variables for saving files
                    raw_image = timestamped_imagename(tn)
                    jpeg_image = timestamped_imagename(tn)[:-4]+".jpg"
                    
                    
                    #TODO:
                    #1. check for the camera capture settings/config file
                    #2. put other camera settings in another call to setup camera (iso, aperture etc) using gphoto2 --set-config

                    # No conversion needed, just take 2 files, 1 jpeg and 1 raw
                    cmd = ["gphoto2 --set-config capturetarget=sdram --capture-image-and-download --filename='"+os.path.join(spool_dir, os.path.splitext(raw_image)[0])+".%C'"]
                    
                    # subprocess.call. shell=True is hellishly insecure and doesn't throw an error if it fails. Needs to be fixed somehow <shrug>
                    subprocess.call(cmd, shell=True)

                    logger.info("Capture Complete")
                    logger.info("Moving and renaming image files, buddy")

                    # glob together all filetypes in filetypes array
                    files = []
                    for filetype in filetypes:
                        files.extend(glob(os.path.join(spool_dir,"*."+filetype.upper())))
                        files.extend(glob(os.path.join(spool_dir,"*."+filetype.lower())))

                    # copying/renaming for files
                    for file in files:
                        # get the extension and basename
                        ext = os.path.splitext(file)[-1].lower()
                        name = os.path.splitext(raw_image)[0]
                        # copy jpegs to the static web dir, and to the upload dir (if upload webcam flag is set)
                        if ext == ".jpeg" or ".jpg":
                            # best to create a symlink to /dev/shm/ from static/
                            shutil.copy(file,os.path.join("static", "dslr_last_image.jpg"))
                            if config.get("ftp","uploadwebcam") == "on":
                                shutil.copy(file,os.path.join(upload_dir, "dslr_last_image.jpg"))
                        # move timestamped image te be uploaded
                        if config.get("ftp","uploadtimestamped")=="on":
                            logger.info("saving timestamped image for you, buddy")
                            os.rename(file, os.path.join(upload_dir, os.path.basename(name+ext)))
                        else:
                            logger.info("deleting file, eh")
                            os.remove(file)
                        logger.info("Captured and stored - %s" % os.path.basename(name+ext))
                    # Log Delay/next shots
                    if next_capture.time() < timestopat:
                        logger.debug("Next capture at %s" % next_capture.isoformat())
                    else:
                        logger.info("Capture will stop at %s" % timestopat.isoformat())

                except Exception, e:
                    next_capture = datetime.datetime.now()
                    # TODO: This needs to catch errors from subprocess.call because it doesn't
                    logger.error("Image Capture error - " + str(e))

            # If the user has specified 'once' then we can stop now
            if (len(args)>0) and (args[0].lower() == "once"):
                break

            time.sleep(0.01)

    except KeyboardInterrupt:
        sys.exit(0)
