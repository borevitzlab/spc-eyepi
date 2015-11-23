__author__ = 'Gareth Dunstone'
import datetime
import json
import logging
import os
import shutil
import subprocess
import time
from configparser import ConfigParser
from glob import glob
from threading import Thread, Event

# default config variables
# TODO: move this to a defaultdict within the camera class
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".JPG"
# Acceptable filetypes
filetypes = ["CR2", "RAW", "NEF", "JPG", "JPEG"]


class GphotoCamera(Thread):
    """
    Camera class
    other cameras inherit from this class.
    """

    def __init__(self, config_filename, name=None, serialnumber=None, camera_port=None):
        # init with name or not, just extending some of the functionality of Thread
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)
        self.stopper = Event()

        if serialnumber != None:
            self.serialnumber = serialnumber
        else:
            self.serialnumber = "[no-cam-sn-detected] wtf?"

        self.camera_port = camera_port

        # variable setting and config file jiggery.
        self.last_config_modify_time = None
        self.config_filename = config_filename
        if not os.path.isfile(self.config_filename):
            eosserial = self.get_eos_serial(self.camera_port)
            self.create_config(name, eosserial=eosserial)
            self.config_filename = os.path.join("configs_byserial", serialnumber + ".ini")
        self.logger = logging.getLogger(self.getName())

        # run setup(), there is a separate setup() function so that it can be called again in the event of settings changing
        self.setup()

    def setup(self):
        # setup new config parser and parse config
        self.config = ConfigParser()
        self.config.read(self.config_filename)
        # accuracy is really the timeout before it gives up and waits for the next time period
        self.accuracy = 3
        # get details from config file
        self.cameraname = self.config["camera"]["name"]
        self.interval = int(float(self.config["timelapse"]["interval"]))
        self.spool_directory = self.config["localfiles"]["spooling_dir"]
        self.upload_directory = self.config["localfiles"]["upload_dir"]
        self.type = "other"
        # DISABLED: apparently we dont need this right now.
        # we kinda do....
        # if self.camera_port:
        #     cmd = ["".join(["gphoto2 --port ", self.camera_port, " --get-config manufacturer"])]
        #     output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True, shell=True)
        #     if "Canon" in output:
        #         self.type = "Canon"
        #     if "Nikon" in output:
        #         self.type = "Nikon"

        # self.exposure_length = self.config.getint("camera","exposure")
        self.last_config_modify_time = os.stat(self.config_filename).st_mtime
        # get enabled
        if self.config["camera"]["enabled"] == "on":
            self.is_enabled = True
        else:
            self.is_enabled = False
        try:
            tval = self.config['timelapse']['starttime']
            if len(tval) == 5:
                if tval[2] == ':':
                    self.timestartfrom = datetime.time(int(tval[:2]), int(tval[3:]))
                    self.logger.info("Starting at %s" % self.timestartfrom.isoformat())
        except Exception as e:
            self.timestartfrom = datetime.time(0, 0)
            self.logger.error("Time conversion error startime - %s" % str(e))
        try:
            tval = self.config['timelapse']['stoptime']
            if len(tval) == 5:
                if tval[2] == ':':
                    self.timestopat = datetime.time(int(tval[:2]), int(tval[3:]))
                    self.logger.info("Stopping at %s" % self.timestopat.isoformat())
        except Exception as e:
            self.timestopat = datetime.time(23, 59)
            self.logger.error("Time conversion error stoptime - %s" % str(e))

        # create spooling and upload directories if they dont exist, and delete files in the spooling dir
        if not os.path.exists(self.spool_directory):
            # All images stored in their own seperate directory
            self.logger.info("Creating Image Storage directory %s" % self.spool_directory)
            os.makedirs(self.spool_directory)
        else:
            for the_file in os.listdir(self.spool_directory):
                file_path = os.path.join(self.spool_directory, the_file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                        self.logger.info("Deleting previous file in the spool eh, Sorry.")
                except Exception as e:
                    self.logger.error("Sorry, buddy! Couldn't delete the files in spool, eh! Error: %s" % e)
        if not os.path.exists(self.upload_directory):
            self.logger.info("creating copyfrom dir %s" % self.upload_directory)
            os.makedirs(self.upload_directory)

    def timestamped_imagename(self, timen):
        """ Build the pathname for a captured image.
            TODO: Remove the need for a default extension!
            Its useless in our extension agnostic capture.
        """
        return os.path.join(self.cameraname + '_' + self.timestamp(timen) + default_extension)

    def time2seconds(self, t):
        """ Convert the time to seconds
            TODO: a better implementation of this such as datetime.timesinceepoch or some sorcery
        """
        return t.hour * 60 * 60 + t.minute * 60 + t.second

    def capture(self, raw_image):
        # try 3 times
        fn = os.path.join(self.spool_directory, os.path.splitext(raw_image)[0]) + ".%C'"
        for tries in range(6):
            if self.type == "Canon":
                # focusmode = (tries % 2) + 1
                # not moving through the focusmode because of choice 2.
                # focusmode
                # Choice: 0 One Shot -- no capture in dark -- autofocus nonmoving
                # Choice: 1 AI Focus -- no capture in dark -- autofocus for movement.
                # Choice: 2 AI Servo -- capture in dark -- apparently this does actually do autofocus...

                # was going to disable autofocus but not now.
                self.logger.info("Capturing with a Canon")
                cmd = ["".join(
                    ["gphoto2 --port ", self.camera_port,
                     " --set-config capturetarget=sdram",
                     " --set-config focusmode=2",
                     " --capture-image-and-download",
                     " --filename='", fn])]

            elif self.type == "Nikon":
                # DISABLED, focusmode set t
                # focusmode2
                # Choice: 0 AF-S -- no capture in dark -- autofocus nonmoving -- use first.
                # Choice: 1 AF-C -- no capture in dark -- autofocus moving
                # Choice: 2 AF-A -- no capture in dark -- autofocus auto
                # Choice: 3 MF (fixed) -- cannot select, this is the switch
                # Choice: 4 MF (selection) -- totally manual -- set to this last.
                self.logger.info("Capturing with a Nikon")
                focusmode = (tries % 2) * 4
                cmd = ["".join(
                    ["gphoto2 --port ", self.camera_port,
                     " --set-config capturetarget=sdram",
                     " --set-config focusmode2=" + str(focusmode),
                     " --capture-image-and-download",
                     " --filename='", fn])]

            else:
                self.logger.info("Capturing with a some other camera")
                cmd = ["".join(
                    ["gphoto2 --port ", self.camera_port,
                     " --set-config capturetarget=sdram",
                     " --capture-image-and-download",
                     " --filename='", fn])]

            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True, shell=True)
                time.sleep(1 + (self.accuracy * 2))
                if "error" in output.lower():
                    raise subprocess.CalledProcessError("non-zero exit status", cmd=cmd, output=output)

                for line in output.splitlines():
                    self.logger.info("GPHOTO2: " + line)
                break
            except subprocess.CalledProcessError as e:
                if tries >= 5:
                    self.logger.critical("Really bad stuff happened. too many tries capturing.")
                    break
                for line in e.output.splitlines():
                    if not line.strip() == "" and not "***" in line:
                        self.logger.error(line.strip())

    def timestamp(self, tn):
        """ Build a timestamp in the required format
        """
        st = tn.strftime('%Y_%m_%d_%H_%M_%S')
        return st

    def create_config(self, serialnumber, eosserial=None):
        if not os.path.exists("configs_byserial"):
            os.makedirs("configs_byserial")
        thiscfg = ConfigParser()
        thiscfg.read("eyepi.ini")
        thiscfg["localfiles"]["spooling_dir"] = os.path.join(thiscfg["localfiles"]["spooling_dir"], serialnumber)
        thiscfg["localfiles"]["upload_dir"] = os.path.join(thiscfg["localfiles"]["upload_dir"], serialnumber)
        thiscfg["camera"]["name"] = thiscfg["camera"]["name"] + "-" + serialnumber
        if eosserial:
            thiscfg["eosserialnumber"]["value"] = str(eosserial)
        with open(os.path.join("configs_byserial", serialnumber + '.ini'), 'w') as configfile:
            thiscfg.write(configfile)

    def get_eos_serial(self, port):
        try:
            cmdret = subprocess.check_output('gphoto2 --port "' + port + '" --get-config eosserialnumber',
                                             shell=True).decode()
            cur = cmdret.split("\n")[-2]
            if cur.startswith("Current:"):
                return cur.split(" ")[-1]
            else:
                return None
        except:
            return None

    def run(self):
        # set the next capture time to now just because
        self.next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified

        while True and not self.stopper.is_set():
            # testing for the config modification
            if os.stat(self.config_filename).st_mtime != self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                # Resetup()
                self.setup()
                self.logger.info("Change in config file at " + datetime.datetime.now().isoformat() + " reloading")

            # set a timenow, this is used everywhere ahead, do not remove.
            tn = datetime.datetime.now()
            # checking if enabled and other stuff
            if (self.time2seconds(tn) % self.interval < self.accuracy) and (tn.time() > self.timestartfrom) and (
                        tn.time() < self.timestopat) and (self.is_enabled):
                try:
                    # set the next capture period to print to the log (not used anymore, really due to time modulo)
                    self.next_capture = tn + datetime.timedelta(seconds=self.interval)
                    # The time now is within the operating times
                    self.logger.info("Capturing Image now for %s" % self.serialnumber)

                    # setting variables for saving files
                    # TODO: Here is where the raw_image should be just timestamped imagename minus extension so that things are more extension agnostic
                    raw_image = self.timestamped_imagename(tn)
                    jpeg_image = self.timestamped_imagename(tn)[:-4] + ".jpg"

                    # TODO: put other camera settings in another call to setup camera (iso, aperture etc) using gphoto2 --set-config (nearly done)

                    # No conversion needed, just take 2 files, 1 jpeg and 1 raw
                    # if self.camera_port:
                    # stuff for checking bulb. not active yet
                    # is_bulbspeed = subprocess.check_output("gphoto2 --port "+self.camera_port+" --get-config shutterspeed", shell=True).splitlines()
                    # bulb = is_bulbspeed[3][is_bulbspeed[3].find("Current: ")+9: len(is_bulbspeed[3])]
                    # if bulb.find("bulb") != -1:
                    #    cmd = ["gphoto2 --port "+ self.camera_port+" --set-config capturetarget=sdram --set-config eosremoterelease=5 --wait-event="+str(self.exposure_length)+"ms --set-config eosremoterelease=11 --wait-event-and-download=2s --filename='"+os.path.join(self.spool_directory, os.path.splitext(raw_image)[0])+".%C'"]
                    # else:
                    # cmd = ["gphoto2 --port "+self.camera_port+" --set-config capturetarget=sdram --capture-image-and-download --wait-event-and-download=36s --filename='"+os.path.join(self.spool_directory, os.path.splitext(raw_image)[0])+".%C'"]

                    self.capture(raw_image)

                    # glob together all filetypes in filetypes array
                    files = []
                    for filetype in filetypes:
                        files.extend(glob(os.path.join(self.spool_directory, "*." + filetype.upper())))
                        files.extend(glob(os.path.join(self.spool_directory, "*." + filetype.lower())))
                    # copying/renaming for files
                    for fn in files:
                        # get the extension and basename
                        ext = os.path.splitext(fn)[-1].lower()
                        name = os.path.splitext(raw_image)[0]
                        # copy jpegs to the static web dir, and to the upload dir (if upload webcam flag is set)

                        try:
                            if ext == ".jpeg" or ".jpg":
                                # best to create a symlink to /dev/shm/ from static/temp
                                shutil.copy(fn, os.path.join("/dev/shm", self.serialnumber + ".jpg"))
                                if self.config["ftp"]["uploadwebcam"] == "on":
                                    shutil.copy(fn, os.path.join(self.upload_directory, "dslr_last_image.jpg"))
                        except Exception as e:
                            self.logger.error("Couldnt copy webcam upload: %s" % str(e))

                        try:
                            if self.config["ftp"]["uploadtimestamped"] == "on":
                                self.logger.debug("saving timestamped image for you, buddy")
                                shutil.copy(fn, os.path.join(self.upload_directory, os.path.basename(name + ext)))
                        except Exception as e:
                            self.logger.error("Couldnt copy timestamp upload: %s" % str(e))
                        try:
                            if os.path.isfile(fn):
                                os.remove(fn)
                        except Exception as e:
                            self.logger.error("Couldnt delete spool file: %s" % str(e))
                        self.logger.info("Captured and stored - %s" % os.path.basename(name + ext))

                    try:
                        with open(self.serialnumber+".json", 'r') as f:
                            js = json.loads(f.read())

                        with open(self.serialnumber+".json", 'w') as f:
                            js['last_capture_time'] = (tn -datetime.datetime.fromtimestamp(0)).total_seconds()-time.daylight*3600
                            js['last_capture_time_human'] = tn.isoformat()
                            f.write(json.dumps(js, indent=4, separators=(',', ': '), sort_keys=True))
                    except Exception as e:
                        self.logger.error("Couldnt log camera capture json why? {}".format(str(e)))

                    # Log Delay/next shots
                    if self.next_capture.time() < self.timestopat:
                        self.logger.info("Next capture at - %s" % self.next_capture.isoformat())
                    else:
                        self.logger.info("Capture will stop at - %s" % self.timestopat.isoformat())
                except Exception as e:
                    self.next_capture = datetime.datetime.now()
                    # TODO: This needs to catch errors from subprocess.call because it doesn't
                    self.logger.error("Image Capture error - " + str(e))


            time.sleep(0.1)

    def stop(self):
        self.stopper.set()


class PiCamera(GphotoCamera):
    """ PiCamera extension to the Camera Class
        extends some functionality and members, modified image capture call and placements.
    """

    def run(self):
        # set next_capture, this isnt really used much anymore except for logging.
        self.next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified
        while True and not self.stopper.is_set():
            # set a timenow this is used locally down here
            tn = datetime.datetime.now()
            # testing for the config modification
            if os.stat(self.config_filename).st_mtime != self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                # Resetup()
                self.setup()
                self.logger.info("change in config at " + datetime.datetime.now().isoformat() + " reloading")

            if (self.time2seconds(tn) % (86400 / 24) < self.accuracy):
                files = []
                # once per hour
                # remove weird images that appear in the working dir.
                # TODO: fix this so its not so hacky, need to find out why the
                # picam is leaving jpegs in the working directoy.
                for filetype in filetypes:
                    files.extend(glob("/home/spc-eyepi/*." + filetype.upper() + "\~"))
                    files.extend(glob("/home/spc-eyepi/*." + filetype.lower() + "\~"))
                    files.extend(glob("/home/spc-eyepi/*." + filetype.upper()))
                    files.extend(glob("/home/spc-eyepi/*." + filetype.lower()))
                for fn in files:
                    os.remove(fn)

            if (self.time2seconds(tn) % self.interval < self.accuracy) and (tn.time() > self.timestartfrom) and (
                        tn.time() < self.timestopat) and (self.is_enabled):
                try:
                    # change the next_capture for logging. not really used much anymore.
                    self.next_capture = tn + datetime.timedelta(seconds=self.interval)

                    # The time now is within the operating times
                    self.logger.info("Capturing Image now for picam")
                    # TODO: once timestamped imagename is more agnostic this will require a jpeg append.
                    image_file = self.timestamped_imagename(tn)

                    image_file = os.path.join(self.spool_directory, image_file)
                    # take the image using os.system(), pretty hacky but it cant exactly be run on windows.
                    if self.config.has_section("picam_size"):
                        os.system("/opt/vc/bin/raspistill -w " + self.config["picam_size"]["width"] + " -h " +
                                  self.config["picam_size"]["height"] + " --nopreview -o " + image_file)
                    else:
                        os.system("/opt/vc/bin/raspistill --nopreview -o " + image_file)
                    os.chmod(image_file, 755)

                    self.logger.debug("Capture Complete")
                    self.logger.debug("Copying the image to the web service, buddy")
                    # Copy the image file to the static webdir
                    try:
                        shutil.copy(image_file, os.path.join("static", "temp", "pi_last_image.jpg"))
                        # webcam copying
                        if self.config["ftp"]["uploadwebcam"] == "on":
                            shutil.copy(image_file, os.path.join(self.upload_directory, "pi_last_image.jpg"))
                    except Exception as e:
                        self.logger.error("Error moving for webinterface or webcam: %s" % str(e))
                    # rename for timestamped upload
                    try:
                        if self.config["ftp"]["uploadtimestamped"] == "on":
                            self.logger.debug("saving timestamped image for you, buddy")
                            shutil.copy(image_file, os.path.join(self.upload_directory, os.path.basename(image_file)))
                    except Exception as e:
                        self.logger.error("Couldnt copy image for timestamped: %s" % str(e))
                    try:
                        self.logger.debug("deleting file buddy")
                        os.remove(image_file)
                    except Exception as e:
                        self.logger.error("Couldnt remove file from filesystem: %s" % str(e))
                    # Do some logging.
                    if self.next_capture.time() < self.timestopat:
                        self.logger.info("Next capture at %s" % self.next_capture.isoformat())
                    else:
                        self.logger.info("Capture will stop at %s" % self.timestopat.isoformat())

                    try:
                        with open("picam.json", 'r') as f:
                            js = json.loads(f.read())

                        with open("picam.json", 'w') as f:
                            js['last_capture_time'] = (tn - datetime.datetime.fromtimestamp(0)).total_seconds()-time.daylight*3600
                            js['last_capture_time_human'] = tn.isoformat()
                            f.write(json.dumps(js, indent=4, separators=(',', ': '), sort_keys=True))
                    except Exception as e:
                        self.logger.error("Couldnt log picam capture json why? {}".format(str(e)))

                except Exception as e:
                    self.next_capture = datetime.datetime.now()
                    self.logger.error("Image Capture error - " + str(e))

            time.sleep(0.1)
