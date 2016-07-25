import datetime
import json
import logging
import os
import shutil
import subprocess
import time
from glob import glob
from threading import Thread, Event
from libs.SysUtil import SysUtil


def import_or_install(package, import_name=None, namespace_name=None):
    try:
        import importlib
        try:
            importlib.import_module(import_name or package)
        except ImportError:
            import pip
            print("Couldn't import package. installing package " + package)
            pip.main(['install', package])
        finally:
            globals()[namespace_name or import_name or package] = importlib.import_module(import_name or package)
    except Exception as e:
        print(
            "couldnt install or import {} from {} for some reason: {}".format(namespace_name or import_name or package,
                                                                              import_name or package, str(e)))


import_or_install("RPi.GPIO", namespace_name="GPIO")
import_or_install("Pillow", import_name="PIL.Image", namespace_name="Image")
import_or_install("picamera")


class Camera(Thread):
    accuracy = 3
    maxw, maxh = 640, 480
    file_types = ["CR2", "RAW", "NEF", "JPG", "JPEG", "PPM"]

    def __init__(self, identifier, *args, queue=None, **kwargs):
        # init with name or not, just extending some of the functionality of Thread
        Thread.__init__(self, name=identifier)
        self.communication_queue = queue
        self.logger = logging.getLogger(self.getName())
        self.stopper = Event()
        self.identifier = identifier
        self.config_filename = SysUtil.identifier_to_ini(self.identifier)
        self.config = \
            self.camera_name = \
            self.interval = \
            self.spool_directory = \
            self.upload_directory = \
            self.begin_capture = \
            self.end_capture = \
            self.begin_capture = \
            self.end_capture = \
            self.current_capture_time = None
        self.failed = list()
        self.re_init()
        SysUtil().add_watch(self.config_filename, self.re_init(), self)

    def re_init(self):
        """
        re-initialisation.
        this causes all the confiuration values to be reacquired, and a config to be recreated as valid if it is broken.
        :return:
        """
        self.logger.info("Re-init...")
        self.config = SysUtil.ensure_config(self.identifier)

        self.camera_name = self.config["camera"]["name"]
        self.interval = self.config.getint("timelapse", "interval")
        self.spool_directory = self.config["localfiles"]["spooling_dir"]
        self.upload_directory = self.config["localfiles"]["upload_dir"]
        self.begin_capture = datetime.time(0, 0)
        self.end_capture = datetime.time(23, 59)
        try:
            self.begin_capture = datetime.time(*map(int, self.config['timelapse']['starttime'].split(":")))
            self.logger.info("Starting capture at {}".format(self.begin_capture.isoformat()))
        except Exception as e:
            self.logger.error("Time conversion error startime - {}".format_map(str(e)))
        try:
            self.end_capture = datetime.time(*map(int, self.config['timelapse']['stoptime'].split(":")))
            self.logger.info("Stopping capture at {}".format(self.end_capture.isoformat()))
        except Exception as e:
            self.logger.error("Time conversion error stoptime - {}".format(str(e)))

        if not os.path.exists(self.spool_directory):
            self.logger.info("Creating spoool dir {}".format(self.spool_directory))
            os.makedirs(self.spool_directory)
        else:
            shutil.rmtree(self.spool_directory)
            os.makedirs(self.spool_directory)

        if not os.path.exists(self.upload_directory):
            self.logger.info("Creating upload dir {}".format(self.upload_directory))
            os.makedirs(self.upload_directory)

        self.current_capture_time = datetime.datetime.now()

    @staticmethod
    def timestamp(tn):
        """
        creates a properly formatted timestamp.
        :param tn:
        :return:
        """
        st = tn.strftime('%Y_%m_%d_%H_%M_%S')
        return st

    @staticmethod
    def time2seconds(t):
        """
        converts a datetime to an integer of seconds since epoch
        """
        try:
            return int(t.timestamp())
        except:
            # only implemented in python3.3
            # this is an old compatibility thing
            return t.hour * 60 * 60 + t.minute * 60 + t.second

    @property
    def timestamped_imagename(self):
        """
        builds a timestamped image basename without extension from a datetime.
        :param time_now:
        :return: string image basename
        """
        return '{camera_name}_{timestamp}'.format(camera_name=self.camera_name,
                                                  timestamp=Camera.timestamp(self.current_capture_time))

    @property
    def time_to_capture(self):
        """
        filters out times for capture, returns True by default
        returns False if the conditions where the camera should NOT capture are met.
        :return:
        """

        current_naive_time = self.current_capture_time.time()

        if not self.config.getboolean("camera", "enabled"):
            # if the camera is disabled, never take photos
            return False

        if self.begin_capture < self.end_capture:
            # where the start capture time is less than the end capture time
            if not self.begin_capture <= current_naive_time <= self.end_capture:
                return False
        else:
            # where the start capture time is greater than the end capture time
            # i.e. capturing across midnight.
            if self.end_capture <= current_naive_time <= self.begin_capture:
                return False

        # capture interval
        if not (self.time2seconds(self.current_capture_time) % self.interval < Camera.accuracy):
            return False
        return True

    def capture(self, image_file_basename):
        """
        capture function.
        override this if rolling a new camera type class
        :param image_file_basename: image basename without ext
        :return: True if capture is successful, otherwise False if retries all failed
        """
        return False

    def stop(self):
        self.stopper.set()

    def communicate_with_updater(self):
        """
        communication member. This is meant to send some metadata to the updater thread.
        :return:
        """
        try:
            data = dict(
                name=self.camera_name,
                identifier=self.identifier,
                failed=self.failed,
                last_capture=int(self.current_capture_time.strftime("%s")))
            self.communication_queue.append(data)
            self.failed = list()
        except Exception as e:
            self.logger.error("thread communication error: {}".format(str(e)))

    def run(self):
        while True and not self.stopper.is_set():

            self.current_capture_time = datetime.datetime.now()
            # checking if enabled and other stuff
            if self.time_to_capture:
                try:
                    raw_image = self.timestamped_imagename

                    self.logger.info("Capturing Image for {}".format(self.identifier))

                    # capture. if capture didnt happen dont continue with the rest.
                    if not self.capture(raw_image):
                        self.failed.append(self.current_capture_time)
                        continue

                    # glob together all filetypes in filetypes array
                    files = []
                    for ft in Camera.file_types:
                        files.extend(glob(os.path.join(self.spool_directory, "*." + ft.upper())))
                        files.extend(glob(os.path.join(self.spool_directory, "*." + ft.lower())))

                    # copying/renaming for files
                    for fn in files:
                        basename = os.path.basename(fn)
                        ext = os.path.splitext(fn)[-1].lower()

                        # copy jpegs to the static web dir, and to the upload dir (if upload webcam flag is set)
                        if ext == ".jpeg" or ext == ".jpg":
                            try:
                                if self.config.getboolean("ftp", "replace"):
                                    if self.config.getboolean("ftp", "resize") and "Image" in globals():
                                        self.logger.info("resizing image {}".format(fn))
                                        im = Image.open(fn)
                                        im.thumbnail((Camera.maxw, Camera.maxh), Image.NEAREST)

                                        im.save(os.path.join("/dev/shm", self.identifier + ".jpg"))
                                    else:
                                        shutil.copy(fn, os.path.join("/dev/shm", self.identifier + ".jpg"))

                                    shutil.copy(os.path.join("/dev/shm", self.identifier + ".jpg"),
                                                os.path.join(self.upload_directory, "last_image.jpg"))
                            except Exception as e:
                                self.logger.error("Couldn't resize for replace upload :( {}".format(str(e)))

                        try:
                            if self.config.getboolean("ftp", "timestamped"):
                                shutil.move(fn, self.upload_directory)
                        except Exception as e:
                            self.logger.error("Couldn't move for timestamped: {}".format(str(e)))

                        self.logger.info("Captured and stored - {}".format(os.path.basename(basename)))

                        try:
                            if os.path.isfile(fn):
                                os.remove(fn)
                        except Exception as e:
                            self.logger.error("Couldn't remove spooled: {}".format(str(e)))

                    self.communicate_with_updater()
                except Exception as e:
                    self.logger.critical("Image Capture error - {}".format(str(e)))
            time.sleep(0.1)


class GphotoCamera(Camera):
    """
    Camera class
    other cameras inherit from this class.
    """

    def __init__(self, identifier, port, **kwargs):
        super(GphotoCamera, self).__init__(identifier, **kwargs)
        # only gphoto cameras have a camera port.
        self.camera_port = port
        self.exposure_length = self.config.get('camera', "exposure")

    def re_init(self):
        super(GphotoCamera, self).re_init()
        self.exposure_length = self.config.getint("camera", "exposure")

    def capture(self, image_file_basename):

        fn = os.path.join(self.spool_directory, image_file_basename) + ".%C"
        cmd = ["gphoto2",
               "--port={}".format(self.camera_port),
               "--set-config=capturetarget=0",
               "--force-overwrite",
               "--capture-image-and-download",
               '--filename={}'.format(fn)
               ]
        self.logger.info("Capture start: {}".format(fn))
        for tries in range(6):
            self.logger.debug("CMD: {}".format(" ".join(cmd)))
            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True)

                if "error" in output.lower():
                    raise subprocess.CalledProcessError("non-zero exit status", cmd=cmd, output=output)
                else:
                    self.logger.info("capture success: {}".format(fn))
                    for line in output.splitlines():
                        self.logger.debug("GPHOTO2: {}".format(line))

                    time.sleep(1 + (self.accuracy * 2))

                    return True

            except subprocess.CalledProcessError as e:
                self.logger.error("failed {} times".format(tries))
                for line in e.output.splitlines():
                    if not line.strip() == "" and "***" not in line:
                        self.logger.error(line.strip())
        else:
            self.logger.critical("Really bad stuff happened. too many tries capturing.")
            return False

    @staticmethod
    def get_eos_serial(port):
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


class WebCamera(Camera):
    def capture(self, raw_image):
        """
        subprocess capture... not efficient.
        todo: check if opencv is imported and use that.
        :param raw_image:
        :return:
        """
        fn = os.path.join(self.spool_directory, os.path.splitext(raw_image)[0]) + ".ppm"
        self.logger.info("Capturing with a USB webcam")
        cmd = ["".join(
            ["streamer",
             " -b 8",
             " -j 100",
             " -s 4224x3156",
             " -o '" + fn + "'"])]

        for tries in range(6):
            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True, shell=True)
                time.sleep(1 + (self.accuracy * 2))

                if "error" in output.lower():
                    raise subprocess.CalledProcessError("non-zero exit status", cmd=cmd, output=output)

                for line in output.splitlines():
                    self.logger.info("STREAMER: " + line)

                return True
            except subprocess.CalledProcessError as e:
                self.logger.error("failed {} times".format(tries))
                for line in e.output.splitlines():
                    if not line.strip() == "" and not "***" in line:
                        self.logger.error(line.strip())
        else:
            self.logger.critical("Really bad stuff happened. too many tries capturing.")
            return False


class PiCamera(Camera):
    """
    Picamera extension to the Camera abstract class.
    """

    def capture(self, image_file_basename):
        if "picamera" in globals():
            try:

                image_file_spoolpath = os.path.join(self.spool_directory, image_file_basename)
                with picamera.PiCamera() as camera:
                    if self.config.has_section("picam_size"):
                        camera.resolution = (self.config.getint("picam_size", "width"),
                                             self.config.getint("picam_size", "height"))

                    camera.start_preview()
                    time.sleep(2)  # Camera warm-up time
                    camera.capture(image_file_spoolpath + ".jpg")
                    return True
            except Exception as e:
                self.logger.critical("EPIC FAIL, trying other method.")

        retcode = 1
        image_file_spoolpath = os.path.join(self.spool_directory, image_file_basename)
        # take the image using os.system(), pretty hacky but its never exactly be run on windows.
        if self.config.has_section("picam_size"):
            w, h = self.config["picam_size"]["width"], self.config["picam_size"]["height"]
            retcode = os.system(
                "/opt/vc/bin/raspistill -w {width} -h {height} --nopreview -o \"{filename}.jpg\"".format(width=w,
                                                                                                         height=h,
                                                                                                         filename=image_file_spoolpath))
        else:
            retcode = os.system(
                "/opt/vc/bin/raspistill --nopreview -o \"{filename}.jpg\"".format(filename=image_file_spoolpath))
        os.chmod(image_file_spoolpath + ".jpg", 755)
        if retcode != 0:
            return False
        return True


class IVPortCamera(PiCamera):
    def setup(self):
        super(IVPortCamera, self).setup()
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(7, GPIO.OUT)
        GPIO.setup(11, GPIO.OUT)
        GPIO.setup(12, GPIO.OUT)

        GPIO.output(7, False)
        GPIO.output(11, False)
        GPIO.output(12, True)

    def capture(self, raw_image):
        map = [[False, False, True],
               [True, False, True],
               [False, True, False],
               [True, True, False]
               ]
        filenames = []

        retcode = 0
        for c in range(0, 4):
            GPIO.setmode(GPIO.BOARD)
            for idx, pin in enumerate([7, 11, 12]):
                GPIO.output(pin, map[c][idx])

            # take the image using os.system(), pretty hacky but it cant exactly be run on windows.
            image_numbered = "{}-{}{}".format(os.path.splitext(raw_image)[0], str(c), os.path.splitext(raw_image)[-1])
            try:
                if self.config.has_section("picam_size"):
                    w, h = self.config["picam_size"]["width"], self.config["picam_size"]["height"]
                    retcode = retcode or os.system(
                        "/opt/vc/bin/raspistill -w {width} -h {height} --nopreview -o \"{filename}\"".format(
                            filename=image_numbered))
                else:
                    retcode = retcode or os.system(
                        "/opt/vc/bin/raspistill --nopreview -o \"{filename}\"".format(filename=image_numbered))
                os.chmod(image_numbered, 755)
            except Exception as e:
                self.logger.critical("Couldn't capture (IVPORT) with camera {} {}".format(c, str(e)))
            filenames.append(image_numbered)
            time.sleep(0.2)

        return filenames
